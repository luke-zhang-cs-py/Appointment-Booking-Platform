"""
routes/coffee_routes.py
------------------------
Coffee chat invites: the host side behind auth, the guest side behind a token.

The split matters. Everything under /api/coffee/invites is an authenticated
host managing their own invites. Everything under /api/coffee/public is the
guest, who has no account and never will -- their only credential is the
token in the link they were emailed.

That token is doing real work, so the public handlers are deliberately
narrow: they resolve exactly one invite, they never accept a host or user id
from the caller, and they never reveal anything about the host beyond the
name and the free slots the guest needs in order to choose. A leaked link
should cost you one coffee chat, not read access to a calendar.
"""

from flask import Blueprint, g, jsonify, request

import coffee_chats
import coffee_notifications
import database as db
from auth import roles_required, token_required
from coffee_chats import InviteError

bp = Blueprint("coffee_routes", __name__, url_prefix="/api/coffee")


def _fail(exc, code=400):
    return jsonify({"error": str(exc)}), code


def _offering_view(invite):
    """The catalogue entry behind this invite, if there is one.

    A guest booking a priced session should see the price before they pick a
    time, not after. An invite with no offering is an ordinary coffee chat and
    this is simply absent.
    """
    if not invite["offering_id"]:
        return None
    import offerings
    row = offerings.get(invite["offering_id"])
    return offerings.public_view(row) if row else None


def _public_view(invite, host):
    """What a guest is allowed to see. Deliberately not the whole row.

    No token echo, no internal ids, no nudge counts -- none of it helps the
    guest pick a time, and all of it is information the link holder has not
    earned.
    """
    return {
        "hostName": host["name"],
        "guestName": invite["guest_name"],
        "guestEmail": invite["guest_email"],
        "topic": invite["topic"] or "Coffee chat",
        "message": invite["message"],
        "durationMin": invite["duration_min"],
        "offering": _offering_view(invite),
        "status": invite["status"],
        "expiresAt": invite["expires_at"],
    }


# ---------------------------------------------------------------- host side

@bp.post("/invites")
@token_required
@roles_required("provider", "admin")
def create_invite():
    try:
        ask = coffee_chats.InviteRequest.from_payload(request.get_json(silent=True))
        invite = coffee_chats.create_invite(g.current_user["id"], ask)
    except InviteError as exc:
        return _fail(exc)

    # Created first, mailed second, on purpose: a mail failure must not lose
    # the invite. The host can resend from the dashboard.
    coffee_notifications.send_invite(invite["id"])
    return jsonify({"invite": coffee_chats.host_view(invite)}), 201


@bp.get("/invites")
@token_required
@roles_required("provider", "admin")
def list_invites():
    status = request.args.get("status")
    invites = coffee_chats.list_for_host(g.current_user["id"], status)
    return jsonify({
        "invites": [coffee_chats.host_view(i) for i in invites],
        "stats": coffee_chats.stats_for_host(g.current_user["id"]),
    })


@bp.post("/invites/<int:invite_id>/nudge")
@token_required
@roles_required("provider", "admin")
def nudge(invite_id):
    invite = coffee_chats.get_invite(invite_id)
    if not invite or invite["host_id"] != g.current_user["id"]:
        return _fail("Invite not found.", 404)
    if not coffee_chats.is_open(invite):
        return _fail("That invite is no longer open.")
    coffee_notifications.send_nudge(invite_id)
    coffee_chats.record_nudge(invite_id)
    return jsonify({"invite": coffee_chats.host_view(coffee_chats.get_invite(invite_id))})


@bp.delete("/invites/<int:invite_id>")
@token_required
@roles_required("provider", "admin")
def revoke(invite_id):
    try:
        invite = coffee_chats.revoke(invite_id, g.current_user["id"])
    except InviteError as exc:
        return _fail(exc, 404 if "not found" in str(exc).lower() else 400)
    return jsonify({"invite": coffee_chats.host_view(invite)})


@bp.post("/run-nudges")
@token_required
@roles_required("admin")
def run_nudges():
    """Manual trigger for the follow-up pass, mirroring the reminder route."""
    return jsonify(coffee_notifications.send_due_nudges())


# --------------------------------------------------------------- guest side

@bp.get("/public/<token>")
def view_invite(token):
    """What the guest sees when they open the link.

    Opening counts as viewing, which is recorded once. A host who can see
    "opened but not booked" knows something different from "never opened",
    and that is the only reason this is tracked.
    """
    invite = coffee_chats.get_by_token(token)
    if not invite:
        return _fail("This invite link is not valid.", 404)

    host = db.query("SELECT * FROM users WHERE id = ?", (invite["host_id"],), one=True)
    if not host:
        return _fail("This invite is no longer available.", 404)

    if coffee_chats.is_open(invite):
        invite = coffee_chats.mark_viewed(invite)

    payload = _public_view(invite, host)
    payload["open"] = coffee_chats.is_open(invite)
    payload["days"] = (coffee_chats.available_slots(invite)
                       if payload["open"] else [])
    return jsonify(payload)


@bp.post("/public/<token>/book")
def book(token):
    body = request.get_json(silent=True) or {}
    try:
        invite, appointment_id = coffee_chats.book(
            token=token,
            date_str=body.get("date"),
            start_time=body.get("time"),
            guest_name=body.get("name"),
            note=body.get("note"),
        )
    except InviteError as exc:
        # 409 for a slot that was taken while the page was open: it is a
        # conflict the guest can resolve by picking again, not a bad request.
        taken = "took that slot" in str(exc)
        return _fail(exc, 409 if taken else 400)

    # The guest gets the standard confirmation, because from their side this is
    # simply a booking. The host gets the coffee-specific one instead of the
    # generic "New booking", since it names the invite it came from -- two
    # emails about one event is how people learn to filter a sender.
    import notifications
    notifications.notify_booked(appointment_id, notify_provider=False)
    coffee_notifications.notify_booked(invite["id"])

    appt = db.query("SELECT * FROM appointments WHERE id = ?", (appointment_id,), one=True)
    return jsonify({
        "booked": True,
        "date": appt["date"],
        "startTime": appt["start_time"],
        "endTime": appt["end_time"],
    }), 201


@bp.post("/public/<token>/decline")
def decline(token):
    body = request.get_json(silent=True) or {}
    try:
        invite = coffee_chats.decline(token, body.get("reason"))
    except InviteError as exc:
        return _fail(exc)
    coffee_notifications.notify_declined(invite["id"])
    return jsonify({"declined": True})
