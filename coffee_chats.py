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
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import database as db
from calendar_logic import is_slot_free, slot_starts_for

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


def _iso(moment):
    """A timestamp in the one format every column in this database uses.

    Deliberately database.TIMESTAMP_FORMAT rather than a literal here: these
    values are compared as strings against created_at, which the schema
    fills in, and the two spellings drifting apart is exactly what made
    due_for_nudge fire early.
    """
    return moment.strftime(db.TIMESTAMP_FORMAT)


def new_token():
    """A URL-safe token with enough entropy that guessing one is hopeless.

    This is the only credential protecting a specific person's booking link,
    so it is sized like a session token rather than a coupon code.
    """
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------- creating

@dataclass
class InviteRequest:
    """The ask, before it has a token or a row.

    create_invite() used to take eight parameters, six of them optional, and
    every caller wrote out the ones it cared about and hoped the rest still
    defaulted sensibly. They describe one thing, so they are one thing. The
    validation that used to sit at the top of create_invite lives here too:
    an InviteRequest that has been checked is a fact about the request, not
    something the next function has to re-establish.
    """

    guest_email: str
    guest_name: str = None
    topic: str = None
    message: str = None
    duration_min: int = DEFAULT_DURATION_MIN
    expiry_days: int = DEFAULT_EXPIRY_DAYS
    offering_id: int = None

    @classmethod
    def from_payload(cls, body):
        """Build one from a JSON request body, in the API's casing.

        Raises InviteError rather than ValueError so a route has one kind of
        failure to catch.
        """
        body = body or {}
        try:
            duration = int(body.get("duration") or DEFAULT_DURATION_MIN)
        except (TypeError, ValueError):
            raise InviteError("Duration must be a number of minutes.")
        return cls(
            guest_email=body.get("email"),
            guest_name=body.get("name"),
            topic=body.get("topic"),
            message=body.get("message"),
            duration_min=duration,
            offering_id=body.get("offeringId"),
        )

    def normalised_email(self):
        """The address, lowercased and trimmed, or an InviteError.

        Deliberately not a full RFC 5322 parser: the only thing worth
        rejecting here is an address that cannot be delivered to at all,
        because the invite is useless without one. Anything subtler is the
        mail server's job.
        """
        email = (self.guest_email or "").strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise InviteError("A valid guest email address is required.")
        return email


def _apply_offering(request, host_id):
    """Let a named offering decide the length and the topic.

    The catalogue entry is the source of truth when there is one, so an
    invite cannot drift out of step with the thing it is an invite to. With
    no offering, the caller's duration has to stand on its own.
    """
    if request.offering_id is None:
        if request.duration_min not in ALLOWED_DURATIONS:
            raise InviteError(
                f"Duration must be one of "
                f"{', '.join(str(d) for d in ALLOWED_DURATIONS)} minutes.")
        return request.duration_min, request.topic

    import offerings as offerings_mod
    offering = offerings_mod.get(request.offering_id)
    if not offering or offering["provider_id"] != host_id:
        raise InviteError("That offering does not belong to you.")
    return offering["duration_min"], request.topic or offering["title"]


def _reject_duplicate(host_id, guest_email):
    """An outstanding invite to the same person is almost always a
    double-send, not a deliberate second ask. Two live links fragment the
    thread and the host loses track of which one was answered."""
    existing = db.query(
        """SELECT id FROM coffee_invites
           WHERE host_id = ? AND guest_email = ? AND status IN ('sent','viewed')
           ORDER BY id DESC""",
        (host_id, guest_email), one=True)
    if existing:
        raise InviteError(
            f"There is already an open invite to {guest_email}. "
            f"Nudge or revoke it before sending another.")


def create_invite(host_id, request):
    """Create an invite from an InviteRequest. Returns the row.

    Does not send anything -- the caller decides when to mail, so that a
    failed send does not lose the invite.
    """
    guest_email = request.normalised_email()
    duration_min, topic = _apply_offering(request, host_id)

    host = db.query("SELECT id FROM users WHERE id = ?", (host_id,), one=True)
    if not host:
        raise InviteError("Host not found.")

    _reject_duplicate(host_id, guest_email)

    expires = _iso(_now() + timedelta(days=request.expiry_days))
    invite_id = db.insert(
        """INSERT INTO coffee_invites
           (host_id, guest_email, guest_name, token, topic, message,
            duration_min, offering_id, status, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?)""",
        (host_id, guest_email, (request.guest_name or "").strip() or None, new_token(),
         (topic or "").strip() or None, (request.message or "").strip() or None,
         duration_min, request.offering_id, expires))
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
    """Bookable start times on the host's calendar, grouped by day.

    Asks for starts that can hold this invite's whole duration, not raw
    slots. Showing a guest 16:45 for a 60-minute session when the day ends at
    17:00 is an invitation to pick it and be told no.

    Reuses calendar_logic rather than reimplementing availability, so blocked
    dates, recurring hours and existing bookings all behave identically to
    the logged-in booking path.
    """
    first = start_from or date.today()
    out = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        day_str = day.isoformat()
        try:
            slots = slot_starts_for(invite["host_id"], day_str,
                                    invite["duration_min"])
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


def _open_invite_for(token):
    """The invite this token can still act on, or the reason it cannot.

    Each state gets its own sentence because "invalid link" covers four
    different situations and only one of them means the guest did anything
    wrong. Somebody who already booked should be told they already booked.
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
    return invite


def _parse_start(date_str, start_time):
    """The requested start as a datetime, refusing anything already gone."""
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
        begin = datetime.strptime(start_time, "%H:%M")
    except (TypeError, ValueError):
        raise InviteError("Pick a date (YYYY-MM-DD) and a time (HH:MM).")
    if datetime.combine(day, begin.time()) < _now():
        raise InviteError("That time is in the past.")
    return begin


def book(token, date_str, start_time, guest_name=None, note=None):
    """Take a slot against an invite. Returns (invite, appointment_id).

    Everything that can be wrong is checked before anything is written: an
    invite that is spent or expired, a malformed date, a slot that somebody
    else took while this page was open. The last one is the realistic race,
    and the unique index on appointments is the backstop if the check and the
    insert are separated by bad luck.
    """
    invite = _open_invite_for(token)
    begin = _parse_start(date_str, start_time)
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


def host_view(invite):
    """An invite as the host sees it, in the API's casing.

    The host endpoints used to return dict(row) straight from SQLite, which
    is snake_case, while the guest endpoints returned camelCase from a
    hand-built dict. Same API, two conventions, decided by which handler you
    happened to hit. This is the one shape.

    The token is included here and nowhere else: the host needs it to copy
    the link, and the guest already has it.
    """
    return {
        "id": invite["id"],
        "guestEmail": invite["guest_email"],
        "guestName": invite["guest_name"],
        "token": invite["token"],
        "topic": invite["topic"],
        "message": invite["message"],
        "durationMin": invite["duration_min"],
        "offeringId": invite["offering_id"],
        "status": invite["status"],
        "appointmentId": invite["appointment_id"],
        "nudgeCount": invite["nudge_count"],
        "lastNudgeAt": invite["last_nudge_at"],
        "viewedAt": invite["viewed_at"],
        "respondedAt": invite["responded_at"],
        "expiresAt": invite["expires_at"],
        "createdAt": invite["created_at"],
    }


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
