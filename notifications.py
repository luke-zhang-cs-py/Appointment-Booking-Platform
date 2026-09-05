"""
What the platform mails people, and when.

Every function here is a *side effect* of something that already happened
(an account was created, a slot was booked, a cancel button was pressed), so
every one of them swallows its own errors: a mail problem must never turn a
successful booking into an error for the person who made it.

Triggers
--------
register                     -> welcome
book an appointment          -> confirmation to the client, heads-up to the provider
cancel an appointment        -> notice to both sides
mark an appointment complete -> thank-you to the client
REMINDER_HOURS_BEFORE the
  appointment starts         -> reminder to both sides (background scheduler)

Delivery, de-duplication, and the SMTP/console decision all live in mailer.py.
"""

import datetime as dt
import logging
import os
import threading

from flask import current_app

import database as db
import mailer

log = logging.getLogger("almanac.notifications")

_scheduler = None
_scheduler_lock = threading.Lock()
_stop = threading.Event()

_APPOINTMENT_SELECT = """
SELECT a.id, a.date, a.start_time, a.end_time, a.status, a.notes,
       p.id AS provider_id, p.name AS provider_name, p.email AS provider_email,
       p.specialty AS provider_specialty,
       c.id AS client_id, c.name AS client_name, c.email AS client_email
FROM appointments a
JOIN users p ON p.id = a.provider_id
JOIN users c ON c.id = a.client_id
"""


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------
def send_welcome(user):
    """New account created."""
    try:
        role_line = {
            "client": "Browse providers, pick an open slot, and it's yours.",
            "provider": "Set your weekly hours under My schedule and clients can start booking.",
            "admin": "You have oversight of every account and every booking.",
        }.get(user["role"], "")

        text, html = _render(
            title="Welcome to Almanac",
            intro=[f"Hi {user['name']}, your {user['role']} account is ready.", role_line],
            details=[("Signed in as", user["email"]), ("Role", user["role"])],
            action=("Open Almanac", _url("/")),
            outro="You'll get an email whenever an appointment is booked, changed, or coming up.",
        )
        mailer.send(
            kind="welcome",
            to=user["email"],
            subject="Welcome to Almanac",
            text=text,
            html=html,
            user_id=user["id"],
        )
    except Exception:
        log.exception("welcome email failed for user %s", user.get("id"))


def notify_booked(appointment_id, notify_provider=True):
    """A client claimed a slot: confirm to them, tell the provider."""
    try:
        appt = _load(appointment_id)
        if not appt:
            return
        when = _when(appt)

        text, html = _render(
            title="Your appointment is confirmed",
            intro=[f"Hi {appt['client_name']}, you're booked in."],
            details=_details(appt, counterpart=("Provider", appt["provider_name"])),
            action=("View my appointments", _url("/")),
            outro="Need to change it? Cancel from your dashboard and book another slot.",
        )
        mailer.send(
            kind="booked_client",
            to=appt["client_email"],
            subject=f"Confirmed: {when} with {appt['provider_name']}",
            text=text,
            html=html,
            appointment_id=appt["id"],
            user_id=appt["client_id"],
        )

        # A coffee chat booking sends its own, richer host email naming the
        # invite it came from. Sending this as well would be two messages about
        # one event, which is how people learn to filter a sender.
        if not notify_provider:
            return

        text, html = _render(
            title="New booking",
            intro=[f"Hi {appt['provider_name']}, a slot on your calendar was just taken."],
            details=_details(appt, counterpart=("Client", appt["client_name"])),
            action=("Open my calendar", _url("/")),
            outro="It's already blocked out on your availability.",
        )
        mailer.send(
            kind="booked_provider",
            to=appt["provider_email"],
            subject=f"New booking: {when} with {appt['client_name']}",
            text=text,
            html=html,
            appointment_id=appt["id"],
            user_id=appt["provider_id"],
        )
    except Exception:
        log.exception("booking emails failed for appointment %s", appointment_id)


def notify_cancelled(appointment_id, cancelled_by=None):
    """An appointment was cancelled: tell both sides who did it."""
    try:
        appt = _load(appointment_id)
        if not appt:
            return
        when = _when(appt)
        by_name = (cancelled_by or {}).get("name")
        by_id = (cancelled_by or {}).get("id")
        by_line = f"Cancelled by {by_name}." if by_name else "This appointment was cancelled."

        for role in ("client", "provider"):
            other = "provider" if role == "client" else "client"
            is_canceller = by_id is not None and by_id == appt[f"{role}_id"]
            text, html = _render(
                title="Appointment cancelled",
                intro=[
                    f"Hi {appt[f'{role}_name']},",
                    "This is confirmation that you cancelled the appointment below."
                    if is_canceller
                    else by_line,
                ],
                details=_details(
                    appt,
                    counterpart=(other.capitalize(), appt[f"{other}_name"]),
                    status="cancelled",
                ),
                action=(
                    ("Book another slot", _url("/"))
                    if role == "client"
                    else ("Open my calendar", _url("/"))
                ),
                outro="The slot is open again for anyone to book."
                if role == "provider"
                else "That time is back in the provider's open slots if you'd like to rebook.",
            )
            mailer.send(
                kind=f"cancelled_{role}",
                to=appt[f"{role}_email"],
                subject=f"Cancelled: {when}",
                text=text,
                html=html,
                appointment_id=appt["id"],
                user_id=appt[f"{role}_id"],
            )
    except Exception:
        log.exception("cancellation emails failed for appointment %s", appointment_id)


def notify_completed(appointment_id):
    """A provider marked the appointment done."""
    try:
        appt = _load(appointment_id)
        if not appt:
            return
        text, html = _render(
            title="Thanks for coming in",
            intro=[
                f"Hi {appt['client_name']}, your appointment with "
                f"{appt['provider_name']} is marked complete."
            ],
            details=_details(
                appt, counterpart=("Provider", appt["provider_name"]), status="completed"
            ),
            action=("Book another appointment", _url("/")),
            outro="",
        )
        mailer.send(
            kind="completed_client",
            to=appt["client_email"],
            subject=f"Completed: {_when(appt)} with {appt['provider_name']}",
            text=text,
            html=html,
            appointment_id=appt["id"],
            user_id=appt["client_id"],
        )
    except Exception:
        log.exception("completion email failed for appointment %s", appointment_id)


def send_test(to, sent_by):
    """Prove the mail settings work without waiting for a real booking."""
    text, html = _render(
        title="Almanac test email",
        intro=[
            f"Sent by {sent_by['name']} from the admin dashboard.",
            "If you're reading this, outgoing mail is configured correctly.",
        ],
        details=[
            ("Transport", "SMTP" if current_app.config["SMTP_HOST"] else "console (no SMTP_HOST)"),
            ("From", current_app.config["MAIL_FROM"]),
            ("Reminders", f"{current_app.config['REMINDER_HOURS_BEFORE']:.0f} h before"),
        ],
        action=("Open Almanac", _url("/")),
    )
    return mailer.send(
        kind="test",
        to=to,
        subject="Almanac test email",
        text=text,
        html=html,
        user_id=sent_by["id"],
    )


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------
def send_due_reminders(now=None):
    """
    Mail everyone whose appointment starts within REMINDER_HOURS_BEFORE.

    Safe to run as often as you like: mailer.send() de-duplicates on
    (appointment, kind, recipient), so each person is reminded exactly once.
    Returns the number of emails queued.
    """
    now = now or dt.datetime.now()
    cutoff = now + dt.timedelta(hours=current_app.config["REMINDER_HOURS_BEFORE"])

    rows = db.query(
        _APPOINTMENT_SELECT
        + " WHERE a.status = 'confirmed' AND (a.date || ' ' || a.start_time) BETWEEN ? AND ?"
        " ORDER BY a.date, a.start_time",
        (now.strftime("%Y-%m-%d %H:%M"), cutoff.strftime("%Y-%m-%d %H:%M")),
    )

    queued = 0
    for appt in rows:
        when = _when(appt)
        lead = _humanise_lead_time(appt, now)

        text, html = _render(
            title="Appointment reminder",
            intro=[f"Hi {appt['client_name']}, your appointment is {lead}."],
            details=_details(appt, counterpart=("Provider", appt["provider_name"])),
            action=("View my appointments", _url("/")),
            outro="If you can't make it, please cancel so someone else can take the slot.",
        )
        queued += bool(
            mailer.send(
                kind="reminder_client",
                to=appt["client_email"],
                subject=f"Reminder: {when} with {appt['provider_name']}",
                text=text,
                html=html,
                appointment_id=appt["id"],
                user_id=appt["client_id"],
            )
        )

        text, html = _render(
            title="Upcoming appointment",
            intro=[f"Hi {appt['provider_name']}, you're seeing {appt['client_name']} {lead}."],
            details=_details(appt, counterpart=("Client", appt["client_name"])),
            action=("Open my calendar", _url("/")),
            outro="",
        )
        queued += bool(
            mailer.send(
                kind="reminder_provider",
                to=appt["provider_email"],
                subject=f"Reminder: {when} with {appt['client_name']}",
                text=text,
                html=html,
                appointment_id=appt["id"],
                user_id=appt["provider_id"],
            )
        )

    if queued:
        log.info("reminder scan queued %s email(s)", queued)
    return queued


def start_reminder_scheduler(app):
    """
    Run send_due_reminders() every REMINDER_SCAN_MINUTES in a daemon thread.

    Deliberately a plain thread rather than Celery/APScheduler: it keeps the
    project dependency-free and one process is plenty at this scale. If you
    ever run several web workers, they'll all scan -- and the unique index on
    email_log means people still only get one message each. (On a bigger
    deployment you'd move this to a cron job hitting
    POST /api/admin/emails/run-reminders instead.)
    """
    global _scheduler

    if not app.config["REMINDERS_ENABLED"]:
        log.info("reminders disabled (REMINDERS_ENABLED=0)")
        return
    # Flask's reloader runs the app twice; let the child process own the timer.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _scheduler_lock:
        if _scheduler is not None and _scheduler.is_alive():
            return

        interval = max(60.0, app.config["REMINDER_SCAN_MINUTES"] * 60)

        def loop():
            _stop.wait(20)  # let the app finish booting before the first scan
            while not _stop.is_set():
                try:
                    with app.app_context():
                        send_due_reminders()
                        # Coffee follow-ups ride the same tick rather than starting a
                        # second timer: one background loop, one place to go wrong.
                        try:
                            import coffee_notifications
                            coffee_notifications.send_due_nudges()
                        except Exception:
                            log.exception('coffee nudge sweep failed')
                except Exception:
                    log.exception("reminder scan failed")
                _stop.wait(interval)

        _scheduler = threading.Thread(target=loop, name="almanac-reminders", daemon=True)
        _scheduler.start()
        log.info(
            "reminder scheduler started: every %.0f min, %.0f h ahead",
            interval / 60,
            app.config["REMINDER_HOURS_BEFORE"],
        )


def stop_reminder_scheduler():
    _stop.set()


# ---------------------------------------------------------------------------
# Loading and formatting
# ---------------------------------------------------------------------------
def _load(appointment_id):
    appt = db.query(_APPOINTMENT_SELECT + " WHERE a.id = ?", (appointment_id,), one=True)
    if not appt:
        log.warning("no appointment %s to notify about", appointment_id)
    return appt


def _url(path):
    return current_app.config["APP_BASE_URL"] + path


def _when(appt):
    """'Mon 8 Sep, 14:30' -- short enough for a subject line."""
    try:
        date = dt.datetime.strptime(appt["date"], "%Y-%m-%d")
    except (ValueError, KeyError, TypeError):
        return f"{appt.get('date', '')} {appt.get('start_time', '')}".strip()
    return f"{date.strftime('%a')} {date.day} {date.strftime('%b')}, {appt['start_time']}"


def _long_date(date_str):
    """'Monday, 8 September 2026' -- for the body of the message."""
    try:
        date = dt.datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return date_str
    return f"{date.strftime('%A')}, {date.day} {date.strftime('%B %Y')}"


def _humanise_lead_time(appt, now):
    """'tomorrow at 14:30' / 'in about 3 hours' / 'today at 14:30'."""
    try:
        start = dt.datetime.strptime(f"{appt['date']} {appt['start_time']}", "%Y-%m-%d %H:%M")
    except ValueError:
        return f"coming up on {appt['date']} at {appt['start_time']}"

    days = (start.date() - now.date()).days
    if days == 0:
        hours = max(1, round((start - now).total_seconds() / 3600))
        if hours <= 4:
            return f"in about {hours} hour{'s' if hours != 1 else ''} (today at {appt['start_time']})"
        return f"today at {appt['start_time']}"
    if days == 1:
        return f"tomorrow at {appt['start_time']}"
    return f"in {days} days, on {_long_date(appt['date'])} at {appt['start_time']}"


def _details(appt, counterpart, status=None):
    rows = [
        counterpart,
        ("Date", _long_date(appt["date"])),
        ("Time", f"{appt['start_time']} – {appt['end_time']}"),
    ]
    if appt.get("provider_specialty") and counterpart[0] == "Provider":
        rows.insert(1, ("Service", appt["provider_specialty"]))
    if appt.get("notes"):
        rows.append(("Notes", appt["notes"]))
    rows.append(("Status", status or appt.get("status", "confirmed")))
    return rows


# ---------------------------------------------------------------------------
# Message layout
# ---------------------------------------------------------------------------
# One structure renders both halves of the multipart email, so the plain-text
# version can never drift out of sync with the HTML one. Styles are inline
# because mail clients strip <style> blocks.
def _render(title, intro, details, action=None, outro=""):
    """Returns (text, html) for a message. `details` is a list of (label, value)."""
    intro_lines = [line for line in intro if line]

    text_parts = [title.upper(), "=" * len(title), ""]
    text_parts += intro_lines + [""]
    width = max((len(label) for label, _ in details), default=0)
    text_parts += [f"  {label.ljust(width)}   {value}" for label, value in details]
    if action:
        text_parts += ["", f"{action[0]}: {action[1]}"]
    if outro:
        text_parts += ["", outro]
    text_parts += ["", "-- Almanac", "You're receiving this because you have an Almanac account."]
    text = "\n".join(text_parts)

    detail_rows = "".join(
        f'<tr>'
        f'<td style="padding:6px 16px 6px 0;color:#8d7a52;font-size:12px;'
        f'text-transform:uppercase;letter-spacing:0.06em;white-space:nowrap;'
        f'vertical-align:top;">{_escape(label)}</td>'
        f'<td style="padding:6px 0;color:#12161d;font-size:15px;'
        f'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{_escape(value)}</td>'
        f'</tr>'
        for label, value in details
    )
    intro_html = "".join(
        f'<p style="margin:0 0 12px;color:#3c4453;font-size:15px;line-height:1.55;">'
        f"{_escape(line)}</p>"
        for line in intro_lines
    )
    action_html = (
        f'<p style="margin:28px 0 0;">'
        f'<a href="{_escape(action[1])}" style="display:inline-block;background:#d9a441;'
        f'color:#201505;text-decoration:none;font-weight:600;font-size:14px;'
        f'padding:11px 20px;border-radius:6px;">{_escape(action[0])}</a></p>'
        if action
        else ""
    )
    outro_html = (
        f'<p style="margin:24px 0 0;color:#6b7280;font-size:13px;line-height:1.5;">'
        f"{_escape(outro)}</p>"
        if outro
        else ""
    )

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#ece6d6;
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
         style="max-width:560px;margin:0 auto;background:#fffdf7;border-radius:10px;
                border:1px solid #ded5bf;overflow:hidden;">
    <tr><td style="height:4px;background:#d9a441;"></td></tr>
    <tr><td style="padding:28px 32px 32px;">
      <p style="margin:0 0 4px;font-size:12px;letter-spacing:0.18em;
                text-transform:uppercase;color:#8d7a52;">Almanac</p>
      <h1 style="margin:0 0 18px;font-size:21px;color:#12161d;font-weight:650;"
          >{_escape(title)}</h1>
      {intro_html}
      <table role="presentation" cellpadding="0" cellspacing="0"
             style="margin:20px 0 0;border-top:1px solid #ece6d6;
                    border-bottom:1px solid #ece6d6;padding:8px 0;width:100%;">
        {detail_rows}
      </table>
      {action_html}
      {outro_html}
    </td></tr>
    <tr><td style="padding:16px 32px;background:#f6f1e4;color:#8d7a52;font-size:12px;
                   border-top:1px solid #ece6d6;">
      You're receiving this because you have an Almanac account.
    </td></tr>
  </table>
</body></html>"""

    return text, html


def _escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
