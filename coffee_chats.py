"""
coffee_chats.py
----------------
Turning an email into a booking.

The rest of Almanac emails people *about* bookings. This is the other
direction: an email that *produces* one. You send somebody an invite, they
click the link in it, they see your real availability, they pick a time. That
is the whole loop.

The design constraint that shapes everything here is that the guest must not
need an account. A coffee chat is usually the first contact you have with
somebody -- a founder, an alum, a hiring manager -- and asking them to
register before they can pick a slot loses most of them. So the invite link
carries a token that authorises exactly one action on exactly one calendar,
and the guest record is created behind the scenes only once they actually
book.

Statuses, in the order they normally happen:

    sent      the invite email went out
    viewed    the guest opened the booking page (tracked so nudges can be
              smarter -- somebody who looked and did not book is a different
              problem from somebody who never opened it)
    booked    a slot was taken; appointment_id points at the real appointment
    declined  the guest said no, explicitly
    expired   the window passed without a response
    revoked   the host withdrew it

Once booked, the appointment is an ordinary Almanac appointment. It gets the
existing confirmation email, the existing 24-hour reminder, and shows up on
the existing dashboards. Nothing downstream needs to know it began as an
invite, which is deliberate: a parallel booking path would be a second thing
to keep correct.
"""

import logging
import secrets
from datetime import date, datetime, timedelta

import database as db
from calendar_logic import get_free_slots, is_slot_free

log = logging.getLogger(__name__)

# How long an invite stays usable. Long enough that somebody can leave it in
# their inbox over a weekend, short enough that a stale link does not hand out
# calendar access indefinitely.
DEFAULT_EXPIRY_DAYS = 14

# Coffee chats are shorter than the platform's general appointments. Thirty
# minutes is the norm; fifteen is a quick intro; sixty is a proper sit-down.
DEFAULT_DURATION_MIN = 30
ALLOWED_DURATIONS = (15, 30, 45, 60)

# How many days of availability the booking page offers. Two weeks is enough
# choice to find a mutual slot without the page becoming a wall of times.
SLOT_WINDOW_DAYS = 14

# Follow-ups. Silence usually means the email was buried, not refused, so one
# reminder is worth sending -- and a second is where persistence turns into
# pestering, which is why the cap is two.
NUDGE_AFTER_DAYS = 3
MAX_NUDGES = 2

# A guest who books without registering still needs a users row, because
# appointments.client_id is a real foreign key. They get one with this role
# and no usable password: they can be emailed and booked, and cannot log in.
GUEST_ROLE = "client"


class InviteError(Exception):
    """Something the caller can act on: bad input, wrong state, taken slot."""


def _now():
    return datetime.now()


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def new_token():
    """A URL-safe token with enough entropy that guessing one is hopeless.

    This is the only credential protecting a specific person's booking link,
    so it is sized like a session token rather than a coupon code.
    """
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------- creating

def create_invite(host_id, guest_email, guest_name=None, topic=None,
                  message=None, duration_min=DEFAULT_DURATION_MIN,
                  expiry_days=DEFAULT_EXPIRY_DAYS):
    """Create an invite. Returns the row. Does not send anything -- the caller
    decides when to mail, so that a failed send does not lose the invite."""
    guest_email = (guest_email or "").strip().lower()
    if "@" not in guest_email or guest_email.startswith("@") or guest_email.endswith("@"):
        raise InviteError("A valid guest email address is required.")

    if duration_min not in ALLOWED_DURATIONS:
        raise InviteError(
            f"Duration must be one of {', '.join(str(d) for d in ALLOWED_DURATIONS)} minutes.")

    host = db.query("SELECT * FROM users WHERE id = ?", (host_id,), one=True)
    if not host:
        raise InviteError("Host not found.")

    # An outstanding invite to the same person is almost always a double-send,
    # not a deliberate second ask. Hand the existing one back so the host can
    # nudge it rather than fragmenting the thread across two links.
    existing = db.query(
        """SELECT * FROM coffee_invites
           WHERE host_id = ? AND guest_email = ? AND status IN ('sent','viewed')
           ORDER BY id DESC""",
        (host_id, guest_email), one=True)
    if existing:
        raise InviteError(
            f"There is already an open invite to {guest_email}. "
            f"Nudge or revoke it before sending another.")

    token = new_token()
    expires = _iso(_now() + timedelta(days=expiry_days))
    invite_id = db.insert(
        """INSERT INTO coffee_invites
           (host_id, guest_email, guest_name, token, topic, message,
            duration_min, status, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'sent', ?)""",
        (host_id, guest_email, (guest_name or "").strip() or None, token,
         (topic or "").strip() or None, (message or "").strip() or None,
         duration_min, expires))
    return get_invite(invite_id)


def get_invite(invite_id):
    return db.query("SELECT * FROM coffee_invites WHERE id = ?", (invite_id,), one=True)


def get_by_token(token):
    return db.query("SELECT * FROM coffee_invites WHERE token = ?", (token,), one=True)


def list_for_host(host_id, status=None):
    if status:
        return db.query(
            """SELECT * FROM coffee_invites WHERE host_id = ? AND status = ?
               ORDER BY created_at DESC""", (host_id, status))
    return db.query(
        "SELECT * FROM coffee_invites WHERE host_id = ? ORDER BY created_at DESC",
        (host_id,))


# ---------------------------------------------------------------- the guest

def is_open(invite):
    """Can this invite still be acted on right now?"""
    if not invite or invite["status"] not in ("sent", "viewed"):
        return False
    return not _is_expired(invite)


def _is_expired(invite):
    try:
        return datetime.fromisoformat(invite["expires_at"]) < _now()
    except (TypeError, ValueError):
        return False


def mark_viewed(invite):
    """Record the first time the guest opened the page.

    Only the first: the point is to distinguish somebody who has seen it from
    somebody who has not, and overwriting on every refresh would lose the
    thing worth knowing.
    """
    if invite["status"] != "sent":
        return invite
    db.execute(
        "UPDATE coffee_invites SET status = 'viewed', viewed_at = ? WHERE id = ?",
        (_iso(_now()), invite["id"]))
    return get_invite(invite["id"])


def available_slots(invite, days=SLOT_WINDOW_DAYS, start_from=None):
    """Free slots on the host's calendar, grouped by day.

    Reuses calendar_logic rather than reimplementing availability, so blocked
    dates, recurring hours and existing bookings all behave identically to the
    logged-in booking path.
    """
    first = start_from or date.today()
    out = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        day_str = day.isoformat()
        try:
            slots = get_free_slots(invite["host_id"], day_str)
        except ValueError:
            continue
        if slots:
            out.append({"date": day_str, "label": _day_label(day), "slots": slots})
    return out


def _day_label(day):
    """'Mon 8 Sep' without a platform-specific format code.

    The obvious spelling is strftime("%a %-d %b"), and the %-d that strips the
    leading zero is a glibc extension: Windows wants %#d and raises
    ValueError: Invalid format string for the other. Building the string from
    the parts avoids having to know which platform this is running on, which
    for something that can deploy to either is the only version worth having.
    """
    return f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"


def _find_or_create_guest(email, name):
    """Look up the guest, creating a login-less account if they are new.

    appointments.client_id is a real foreign key, so a guest needs a row. They
    get one with an unusable password hash: reachable by email, bookable,
    unable to log in. If they later register properly, the address already
    exists and their history comes with them.
    """
    user = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
    if user:
        return user

    display = (name or email.split("@")[0].replace(".", " ").title())
    db.insert(
        """INSERT INTO users (name, email, password_hash, role)
           VALUES (?, ?, ?, ?)""",
        (display, email, "!invite-only", GUEST_ROLE))
    return db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)


def book(token, date_str, start_time, guest_name=None, note=None):
    """Take a slot against an invite. Returns (invite, appointment_id).

    Everything that can be wrong is checked before anything is written: an
    invite that is spent or expired, a malformed date, a slot that somebody
    else took while this page was open. The last one is the realistic race,
    and the unique index on appointments is the backstop if the check and the
    insert are separated by bad luck.
    """
    invite = get_by_token(token)
    if not invite:
        raise InviteError("This invite link is not valid.")
    if _is_expired(invite):
        expire(invite["id"])
        raise InviteError("This invite has expired. Ask your host for a new link.")
    if invite["status"] == "booked":
        raise InviteError("This invite has already been used to book a time.")
    if invite["status"] not in ("sent", "viewed"):
        raise InviteError("This invite is no longer active.")

    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
        begin = datetime.strptime(start_time, "%H:%M")
    except (TypeError, ValueError):
        raise InviteError("Pick a date (YYYY-MM-DD) and a time (HH:MM).")

    if datetime.combine(day, begin.time()) < _now():
        raise InviteError("That time is in the past.")

    end_time = (begin + timedelta(minutes=invite["duration_min"])).strftime("%H:%M")

    if not is_slot_free(invite["host_id"], date_str, start_time, end_time):
        raise InviteError("Someone just took that slot. Please pick another.")

    guest = _find_or_create_guest(invite["guest_email"],
                                  guest_name or invite["guest_name"])

    topic = invite["topic"] or "Coffee chat"
    notes = f"Coffee chat: {topic}"
    if note:
        notes += f"\n\nFrom {guest['name']}: {note.strip()}"

    try:
        appointment_id = db.insert(
            """INSERT INTO appointments
               (provider_id, client_id, date, start_time, end_time, status, notes)
               VALUES (?, ?, ?, ?, ?, 'confirmed', ?)""",
            (invite["host_id"], guest["id"], date_str, start_time, end_time, notes))
    except Exception as exc:                       # unique-constraint backstop
        db.rollback()
        log.info("coffee booking lost a race: %s", exc)
        raise InviteError("Someone just took that slot. Please pick another.")

    db.execute(
        """UPDATE coffee_invites
           SET status = 'booked', appointment_id = ?, responded_at = ?,
               guest_name = COALESCE(?, guest_name)
           WHERE id = ?""",
        (appointment_id, _iso(_now()), (guest_name or "").strip() or None,
         invite["id"]))
    return get_invite(invite["id"]), appointment_id


def decline(token, reason=None):
    invite = get_by_token(token)
    if not invite:
        raise InviteError("This invite link is not valid.")
    if invite["status"] == "booked":
        raise InviteError("This invite has already been used to book a time.")
    if not is_open(invite):
        raise InviteError("This invite is no longer active.")
    db.execute(
        """UPDATE coffee_invites SET status = 'declined', responded_at = ?,
           message = COALESCE(?, message) WHERE id = ?""",
        (_iso(_now()), (reason or "").strip() or None, invite["id"]))
    return get_invite(invite["id"])


# ------------------------------------------------------------- housekeeping

def revoke(invite_id, host_id):
    invite = get_invite(invite_id)
    if not invite or invite["host_id"] != host_id:
        raise InviteError("Invite not found.")
    if invite["status"] == "booked":
        raise InviteError("That invite is already booked. Cancel the appointment instead.")
    db.execute("UPDATE coffee_invites SET status = 'revoked' WHERE id = ?", (invite_id,))
    return get_invite(invite_id)


def expire(invite_id):
    db.execute(
        "UPDATE coffee_invites SET status = 'expired' WHERE id = ? AND status IN ('sent','viewed')",
        (invite_id,))


def expire_stale(now=None):
    """Move past-expiry invites out of the open states. Returns how many."""
    stamp = _iso(now or _now())
    rows = db.query(
        """SELECT id FROM coffee_invites
           WHERE status IN ('sent','viewed') AND expires_at < ?""", (stamp,))
    for row in rows:
        expire(row["id"])
    return len(rows)


def due_for_nudge(now=None):
    """Invites that have gone quiet long enough to deserve one follow-up.

    Measured from the last contact, not from creation, so a nudged invite
    waits the full interval again instead of being chased daily.
    """
    now = now or _now()
    cutoff = _iso(now - timedelta(days=NUDGE_AFTER_DAYS))
    return db.query(
        """SELECT * FROM coffee_invites
           WHERE status IN ('sent','viewed')
             AND nudge_count < ?
             AND expires_at > ?
             AND COALESCE(last_nudge_at, created_at) < ?
           ORDER BY created_at""",
        (MAX_NUDGES, _iso(now), cutoff))


def record_nudge(invite_id):
    db.execute(
        """UPDATE coffee_invites
           SET nudge_count = nudge_count + 1, last_nudge_at = ?
           WHERE id = ?""",
        (_iso(_now()), invite_id))


def stats_for_host(host_id):
    """Counts by status, so the dashboard can say what happened to what."""
    rows = db.query(
        "SELECT status, COUNT(*) AS n FROM coffee_invites WHERE host_id = ? GROUP BY status",
        (host_id,))
    counts = {r["status"]: r["n"] for r in rows}
    sent = sum(counts.values())
    booked = counts.get("booked", 0)
    return {
        "total": sent,
        "byStatus": counts,
        "bookedRate": round(booked / sent, 3) if sent else None,
    }
