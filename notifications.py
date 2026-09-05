"""
What the platform mails people, and why.

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
  appointment starts         -> reminder to both sides (scheduler.py drives this)

This module used to be five hundred lines because it also owned the message
layout and the background timer. Those are now email_render.py and
scheduler.py, which leaves this one answering a single question: what does
Almanac say, and on what occasion. Delivery, de-duplication and the
SMTP/console decision live in mailer.py.
"""

import datetime as dt
import logging

from flask import current_app

import database as db
import mailer
from email_render import details, lead_time, render, url, when

log = logging.getLogger("almanac.notifications")

_APPOINTMENT_SELECT = """
SELECT a.id, a.date, a.start_time, a.end_time, a.status, a.notes,
       p.id AS provider_id, p.name AS provider_name, p.email AS provider_email,
       p.specialty AS provider_specialty,
       c.id AS client_id, c.name AS client_name, c.email AS client_email
FROM appointments a
JOIN users p ON p.id = a.provider_id
JOIN users c ON c.id = a.client_id
"""


def load_appointment(appointment_id):
    """An appointment with both parties attached: the shape every email wants.

    Public because coffee_notifications needs exactly this and used to import
    it as `_load`. One module reaching into another's privates is worth
    avoiding; the same three-table join written twice is worth avoiding more.
    """
    appt = db.query(_APPOINTMENT_SELECT + " WHERE a.id = ?", (appointment_id,), one=True)
    if not appt:
        log.warning("no appointment %s to notify about", appointment_id)
    return appt


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

        text, html = render(
            title="Welcome to Almanac",
            intro=[f"Hi {user['name']}, your {user['role']} account is ready.", role_line],
            details=[("Signed in as", user["email"]), ("Role", user["role"])],
            action=("Open Almanac", url("/")),
            outro="You'll get an email whenever an appointment is booked, changed, or coming up.",
        )
        mailer.send(mailer.Message(
            kind="welcome",
            to=user["email"],
            subject="Welcome to Almanac",
            text=text,
            html=html,
            user_id=user["id"],
        ))
    except Exception:
        log.exception("welcome email failed for user %s", user.get("id"))


def notify_booked(appointment_id, notify_provider=True):
    """A client claimed a slot: confirm to them, tell the provider."""
    try:
        appt = load_appointment(appointment_id)
        if not appt:
            return
        moment = when(appt)

        text, html = render(
            title="Your appointment is confirmed",
            intro=[f"Hi {appt['client_name']}, you're booked in."],
            details=details(appt, counterpart=("Provider", appt["provider_name"])),
            action=("View my appointments", url("/")),
            outro="Need to change it? Cancel from your dashboard and book another slot.",
        )
        mailer.send(mailer.Message(
            kind="booked_client",
            to=appt["client_email"],
            subject=f"Confirmed: {moment} with {appt['provider_name']}",
            text=text,
            html=html,
            appointment_id=appt["id"],
            user_id=appt["client_id"],
        ))

        # A coffee chat booking sends its own, richer host email naming the
        # invite it came from. Sending this as well would be two messages about
        # one event, which is how people learn to filter a sender.
        if not notify_provider:
            return

        text, html = render(
            title="New booking",
            intro=[f"Hi {appt['provider_name']}, a slot on your calendar was just taken."],
            details=details(appt, counterpart=("Client", appt["client_name"])),
            action=("Open my calendar", url("/")),
            outro="It's already blocked out on your availability.",
        )
        mailer.send(mailer.Message(
            kind="booked_provider",
            to=appt["provider_email"],
            subject=f"New booking: {moment} with {appt['client_name']}",
            text=text,
            html=html,
            appointment_id=appt["id"],
            user_id=appt["provider_id"],
        ))
    except Exception:
        log.exception("booking emails failed for appointment %s", appointment_id)


def notify_cancelled(appointment_id, cancelled_by=None):
    """An appointment was cancelled: tell both sides who did it."""
    try:
        appt = load_appointment(appointment_id)
        if not appt:
            return
        for role in ("client", "provider"):
            _send_cancellation(appt, role, cancelled_by or {})
    except Exception:
        log.exception("cancellation emails failed for appointment %s", appointment_id)


def _send_cancellation(appt, role, cancelled_by):
    """One half of a cancellation.

    Both sides are told; only one of them is told that they were the one who
    did it, which is the difference between a confirmation and a surprise.
    """
    other = "provider" if role == "client" else "client"
    by_name = cancelled_by.get("name")
    is_canceller = (cancelled_by.get("id") is not None
                    and cancelled_by["id"] == appt[f"{role}_id"])

    if is_canceller:
        second_line = "This is confirmation that you cancelled the appointment below."
    elif by_name:
        second_line = f"Cancelled by {by_name}."
    else:
        second_line = "This appointment was cancelled."

    if role == "provider":
        action = ("Open my calendar", url("/"))
        outro = "The slot is open again for anyone to book."
    else:
        action = ("Book another slot", url("/"))
        outro = "That time is back in the provider's open slots if you'd like to rebook."

    text, html = render(
        title="Appointment cancelled",
        intro=[f"Hi {appt[f'{role}_name']},", second_line],
        details=details(appt,
                        counterpart=(other.capitalize(), appt[f"{other}_name"]),
                        status="cancelled"),
        action=action,
        outro=outro,
    )
    mailer.send(mailer.Message(
        kind=f"cancelled_{role}",
        to=appt[f"{role}_email"],
        subject=f"Cancelled: {when(appt)}",
        text=text,
        html=html,
        appointment_id=appt["id"],
        user_id=appt[f"{role}_id"],
    ))


def notify_completed(appointment_id):
    """A provider marked the appointment done."""
    try:
        appt = load_appointment(appointment_id)
        if not appt:
            return
        text, html = render(
            title="Thanks for coming in",
            intro=[
                f"Hi {appt['client_name']}, your appointment with "
                f"{appt['provider_name']} is marked complete."
            ],
            details=details(
                appt, counterpart=("Provider", appt["provider_name"]), status="completed"
            ),
            action=("Book another appointment", url("/")),
            outro="",
        )
        mailer.send(mailer.Message(
            kind="completed_client",
            to=appt["client_email"],
            subject=f"Completed: {when(appt)} with {appt['provider_name']}",
            text=text,
            html=html,
            appointment_id=appt["id"],
            user_id=appt["client_id"],
        ))
    except Exception:
        log.exception("completion email failed for appointment %s", appointment_id)


def send_test(to, sent_by):
    """Prove the mail settings work without waiting for a real booking."""
    text, html = render(
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
        action=("Open Almanac", url("/")),
    )
    return mailer.send(mailer.Message(
        kind="test",
        to=to,
        subject="Almanac test email",
        text=text,
        html=html,
        user_id=sent_by["id"],
    ))


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

    queued = sum(_remind_both_sides(appt, now) for appt in rows)
    if queued:
        log.info("reminder scan queued %s email(s)", queued)
    return queued


def _remind_both_sides(appt, now):
    """The two reminders for one appointment. Returns how many were queued."""
    moment = when(appt)
    lead = lead_time(appt, now)

    text, html = render(
        title="Appointment reminder",
        intro=[f"Hi {appt['client_name']}, your appointment is {lead}."],
        details=details(appt, counterpart=("Provider", appt["provider_name"])),
        action=("View my appointments", url("/")),
        outro="If you can't make it, please cancel so someone else can take the slot.",
    )
    queued = int(bool(mailer.send(mailer.Message(
        kind="reminder_client",
        to=appt["client_email"],
        subject=f"Reminder: {moment} with {appt['provider_name']}",
        text=text,
        html=html,
        appointment_id=appt["id"],
        user_id=appt["client_id"],
    ))))

    text, html = render(
        title="Upcoming appointment",
        intro=[f"Hi {appt['provider_name']}, you're seeing {appt['client_name']} {lead}."],
        details=details(appt, counterpart=("Client", appt["client_name"])),
        action=("Open my calendar", url("/")),
        outro="",
    )
    queued += int(bool(mailer.send(mailer.Message(
        kind="reminder_provider",
        to=appt["provider_email"],
        subject=f"Reminder: {moment} with {appt['client_name']}",
        text=text,
        html=html,
        appointment_id=appt["id"],
        user_id=appt["provider_id"],
    ))))
    return queued
