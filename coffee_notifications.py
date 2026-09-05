"""
coffee_notifications.py
------------------------
The emails that carry a coffee chat from ask to booking.

Separate from notifications.py for two reasons. That module is already five
hundred lines and covers the account lifecycle; adding another two hundred
would make the file the thing you scroll past to find anything. And these
messages have a genuinely different audience: every other email Almanac sends
goes to somebody who has an account, and these go to somebody who may never
have heard of it.

That audience difference drives the wording. The invite leads with who is
asking and why, mentions the platform second, and carries a link that works
without a login. Nothing says "sign in to your dashboard", because for a
first-contact email that is the fastest way to lose the reader.

The rendering helpers are shared with notifications.py rather than
duplicated. They are underscore-prefixed there, and importing them anyway is
the lesser evil: two copies of the house email style would drift, and the
first thing to diverge would be the footer nobody reads until it is wrong.
"""

import logging

import coffee_chats
import database as db
import mailer
from notifications import _details, _load, _long_date, _render, _url, _when

log = logging.getLogger(__name__)


def _load_invite(invite_id):
    return db.query("SELECT * FROM coffee_invites WHERE id = ?", (invite_id,), one=True)


def _host_of(invite):
    return db.query("SELECT * FROM users WHERE id = ?", (invite["host_id"],), one=True)


def send_invite(invite_id):
    """The ask. One link, no signup, no instructions to follow."""
    try:
        invite = _load_invite(invite_id)
        if not invite:
            return
        host = _host_of(invite)
        if not host:
            return

        topic = invite["topic"] or "a coffee chat"
        greeting = f"Hi {invite['guest_name']}," if invite["guest_name"] else "Hi,"

        intro = [greeting, f"{host['name']} would like to set up {topic} with you."]
        if invite["message"]:
            intro.append(f"“{invite['message']}”")
        intro.append("Pick whatever time suits you — no account needed.")

        text, html = _render(
            title=f"{host['name']} would like a coffee chat",
            intro=intro,
            details=[
                ("With", f"{host['name']} ({host['email']})"),
                ("Length", f"{invite['duration_min']} minutes"),
                ("Topic", invite["topic"] or "Coffee chat"),
                ("Link expires", _long_date(invite["expires_at"][:10])),
            ],
            action=("Pick a time", _url(f"/coffee/{invite['token']}")),
            outro="If now is not a good time, the same link lets you say so.",
        )
        mailer.send(
            kind="coffee_invite",
            to=invite["guest_email"],
            subject=f"{host['name']} would like to grab a coffee chat",
            text=text,
            html=html,
        )
    except Exception:
        log.exception("coffee invite email failed for invite %s", invite_id)


def send_nudge(invite_id):
    """One follow-up, written to be easy to ignore.

    Somebody who has not replied in three days is busy, not hostile. The
    wording offers an out rather than applying pressure, and MAX_NUDGES stops
    this after the second attempt.
    """
    try:
        invite = _load_invite(invite_id)
        if not invite:
            return
        host = _host_of(invite)
        if not host:
            return

        # Opened-but-not-booked is a different problem from never-opened, and
        # the invite tracks which, so the follow-up can at least not be wrong
        # about what happened.
        seen = invite["status"] == "viewed"
        opener = ("Just floating this back up — the link is still open."
                  if seen else
                  "This may have landed at a busy moment, so here it is again.")

        text, html = _render(
            title="Still up for a coffee chat?",
            intro=[
                f"Hi {invite['guest_name'] or 'there'},",
                opener,
                f"{host['name']} has time set aside if you would like it.",
            ],
            details=[
                ("With", host["name"]),
                ("Length", f"{invite['duration_min']} minutes"),
                ("Link expires", _long_date(invite["expires_at"][:10])),
            ],
            action=("Pick a time", _url(f"/coffee/{invite['token']}")),
            outro="No reply needed if the timing does not work — the invite "
                  "closes itself.",
        )
        mailer.send(
            kind="coffee_nudge",
            to=invite["guest_email"],
            subject=f"Still up for a coffee chat with {host['name']}?",
            text=text,
            html=html,
        )
    except Exception:
        log.exception("coffee nudge email failed for invite %s", invite_id)


def notify_booked(invite_id):
    """Tell the host their invite converted.

    The guest already receives the ordinary booking confirmation, because a
    coffee chat becomes a normal appointment the moment it is booked. This is
    the half that would otherwise be missing: the host finding out that an
    invite they sent days ago just landed.
    """
    try:
        invite = _load_invite(invite_id)
        if not invite or not invite["appointment_id"]:
            return
        appt = _load(invite["appointment_id"])
        if not appt:
            return

        text, html = _render(
            title="Your coffee chat invite was accepted",
            intro=[
                f"Hi {appt['provider_name']},",
                f"{appt['client_name']} picked a time from the invite you sent.",
            ],
            details=_details(appt, counterpart=("Guest", appt["client_name"])) + [
                ("Topic", invite["topic"] or "Coffee chat"),
                ("Invited", _long_date(invite["created_at"][:10])),
            ],
            action=("Open Almanac", _url("/")),
            outro="It is on your calendar now, with a reminder the day before.",
        )
        mailer.send(
            kind="coffee_booked",
            to=appt["provider_email"],
            subject=f"Coffee chat booked: {_when(appt)} with {appt['client_name']}",
            text=text,
            html=html,
            appointment_id=appt["id"],
            user_id=appt["provider_id"],
        )
    except Exception:
        log.exception("coffee booked email failed for invite %s", invite_id)


def notify_declined(invite_id):
    """Tell the host it was a no, so they are not left waiting on it."""
    try:
        invite = _load_invite(invite_id)
        if not invite:
            return
        host = _host_of(invite)
        if not host:
            return

        details = [("Guest", invite["guest_email"]),
                   ("Topic", invite["topic"] or "Coffee chat")]
        if invite["message"]:
            details.append(("They said", invite["message"]))

        text, html = _render(
            title="Coffee chat invite declined",
            intro=[
                f"Hi {host['name']},",
                f"{invite['guest_name'] or invite['guest_email']} is not able to "
                f"take up the invite.",
            ],
            details=details,
            action=("Open Almanac", _url("/")),
            outro="No slot was ever held, so nothing needs freeing up.",
        )
        mailer.send(
            kind="coffee_declined",
            to=host["email"],
            subject="Coffee chat invite declined",
            text=text,
            html=html,
            user_id=host["id"],
        )
    except Exception:
        log.exception("coffee declined email failed for invite %s", invite_id)


def send_due_nudges(now=None):
    """Follow up on quiet invites and close out expired ones.

    Called from the existing reminder scheduler tick rather than a second
    timer, so the process keeps one background loop and one place for it to
    go wrong.
    """
    closed = coffee_chats.expire_stale(now)
    due = coffee_chats.due_for_nudge(now)
    for invite in due:
        send_nudge(invite["id"])
        coffee_chats.record_nudge(invite["id"])
    if due or closed:
        log.info("coffee: %d nudge(s) sent, %d invite(s) expired", len(due), closed)
    return {"nudged": len(due), "expired": closed}
