"""
Outbound email transport.

Two things live in this module:

* **A transport.** Talks SMTP through the standard library -- no extra
  dependency. When ``SMTP_HOST`` is empty (the local-development default) the
  message is written to the server log instead, so you can watch the whole
  notification flow work without owning a mail server.
* **A queue.** Every message is handed to a background worker thread, so an
  API request never waits on a mail server and a bounced connection can never
  turn a successful booking into a 500.

Every attempt is recorded in the ``email_log`` table. That log is also the
de-duplication mechanism: a unique index over
``(appointment_id, kind, recipient)`` means a given person can only ever be
mailed once about a given appointment for a given reason -- overlapping
reminder scans, a double-clicked button, or two web workers running at the
same time all collapse into one message.

What goes *in* those messages is notifications.py and
coffee_notifications.py; how they are laid out is email_render.py.
"""

import datetime as dt
import logging
import queue
import smtplib
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from flask import current_app

import database as db

log = logging.getLogger("almanac.mail")

try:
    import psycopg2

    INTEGRITY_ERRORS = (sqlite3.IntegrityError, psycopg2.IntegrityError)
except ImportError:
    INTEGRITY_ERRORS = (sqlite3.IntegrityError,)


@dataclass
class Message:
    """One outbound email, already rendered.

    This used to be seven positional arguments to send(), which then built a
    smaller version of this dataclass out of them. The arguments were an
    object all along: they travel together, none of them means anything
    alone, and every caller wrote them out in the same order. Naming it means
    a call site reads as one thing being described rather than seven being
    passed in the right sequence.

    ``log_id`` is filled in by send() once the message is claimed in
    email_log. Callers do not set it.
    """

    kind: str
    to: str
    subject: str
    text: str
    html: str = ""
    appointment_id: int = None
    user_id: int = None
    log_id: int = None


_queue = queue.Queue()
_worker = None
_worker_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def send(message):
    """
    Queue one Message. Returns True if it was accepted for delivery.

    Returns False -- without raising -- when mail is switched off, when there
    is no address to send to, or when this exact message has already been sent
    for this appointment. Callers are notification side-effects: they should
    never be able to fail the request that triggered them.
    """
    if not current_app.config["MAIL_ENABLED"]:
        log.info("mail disabled, skipping %s to %s", message.kind, message.to)
        return False
    if not message.to:
        return False

    message.log_id = _claim(message)
    if message.log_id is None:
        log.debug("%s already sent to %s for appointment %s",
                  message.kind, message.to, message.appointment_id)
        return False

    if _worker is not None and _worker.is_alive():
        _queue.put(message)
    else:
        # No worker running (a script, a test): deliver inline instead.
        _process(message)
    return True


def init_app(app):
    """Start the delivery worker. Safe to call more than once."""
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(
            target=_worker_loop, args=(app,), name="almanac-mailer", daemon=True
        )
        _worker.start()


def wait_until_idle(timeout=15.0):
    """
    Block until the queue has drained. Only useful to scripts and tests --
    the web app never needs to wait for mail.
    """
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _queue.unfinished_tasks


# ---------------------------------------------------------------------------
# email_log bookkeeping
# ---------------------------------------------------------------------------
def _claim(message):
    """
    Reserve the right to send this message, returning the email_log row id.

    Returns None if somebody already claimed it. A previous attempt that
    *failed* is handed back for another try, so a mail server outage doesn't
    permanently swallow a reminder.
    """
    try:
        return db.insert(
            "INSERT INTO email_log (kind, recipient, subject, appointment_id, user_id, status) "
            "VALUES (?, ?, ?, ?, ?, 'queued')",
            (message.kind, message.to, message.subject,
             message.appointment_id, message.user_id),
        )
    except INTEGRITY_ERRORS:
        db.rollback()

    existing = db.query(
        "SELECT id, status FROM email_log "
        "WHERE appointment_id = ? AND kind = ? AND recipient = ?",
        (message.appointment_id, message.kind, message.to),
        one=True,
    )
    if existing and existing["status"] == "failed":
        db.execute(
            "UPDATE email_log SET status = 'queued', error = NULL WHERE id = ?",
            (existing["id"],),
        )
        return existing["id"]
    return None


def _mark(log_id, status, error=None):
    if log_id is None:
        return
    db.execute(
        "UPDATE email_log SET status = ?, error = ?, sent_at = ? WHERE id = ?",
        (status, error, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), log_id),
    )


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def _worker_loop(app):
    while True:
        message = _queue.get()
        try:
            with app.app_context():
                _process(message)
        except Exception:  # never let the worker thread die
            log.exception("mailer worker crashed handling %s", message.kind)
        finally:
            _queue.task_done()


def _process(message):
    try:
        _deliver(message)
    except Exception as exc:
        log.warning("failed to send %s to %s: %s", message.kind, message.to, exc)
        _mark(message.log_id, "failed", f"{type(exc).__name__}: {exc}")
        return False
    _mark(message.log_id, "sent")
    log.info("sent %s to %s", message.kind, message.to)
    return True


def _build(message):
    cfg = current_app.config
    msg = EmailMessage()
    msg["Subject"] = message.subject
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = message.to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="almanac.local")
    if cfg["MAIL_REPLY_TO"]:
        msg["Reply-To"] = cfg["MAIL_REPLY_TO"]
    # Transactional mail: keep it out of conversation threads and auto-replies.
    msg["Auto-Submitted"] = "auto-generated"

    msg.set_content(message.text)
    if message.html:
        msg.add_alternative(message.html, subtype="html")
    return msg


def _deliver(message):
    cfg = current_app.config
    msg = _build(message)

    host = cfg["SMTP_HOST"]
    if not host:
        _log_instead_of_sending(message)
        return

    if cfg["SMTP_USE_SSL"]:
        smtp = smtplib.SMTP_SSL(host, cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT"])
    else:
        smtp = smtplib.SMTP(host, cfg["SMTP_PORT"], timeout=cfg["SMTP_TIMEOUT"])

    with smtp:
        smtp.ehlo()
        if cfg["SMTP_USE_TLS"] and not cfg["SMTP_USE_SSL"]:
            smtp.starttls()
            smtp.ehlo()
        if cfg["SMTP_USERNAME"]:
            smtp.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"])
        smtp.send_message(msg)


def _log_instead_of_sending(message):
    """The no-SMTP-configured path: print the message where a developer sees it."""
    rule = "-" * 68
    body = "\n".join(
        [
            "",
            rule,
            f"  MAIL (not sent -- SMTP_HOST is unset)  [{message.kind}]",
            rule,
            f"  To:      {message.to}",
            f"  Subject: {message.subject}",
            rule,
            message.text,
            rule,
            "",
        ]
    )
    _print(body)


def _print(body):
    """print(), minus the one way it can fail.

    Message bodies contain characters that are not in every console codepage
    -- the en dash between a start and end time is enough. Under cp437 or a
    bare C locale, printing one raises UnicodeEncodeError, _process catches
    it, and the email is recorded as *failed* even though in this mode
    "sending" only ever meant printing. A console that cannot draw a dash is
    not a delivery failure, so it degrades to whatever that console can show.
    """
    try:
        print(body, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(body.encode(encoding, "replace").decode(encoding, "replace"), flush=True)
