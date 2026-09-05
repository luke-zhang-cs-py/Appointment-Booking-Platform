"""
routes/availability_routes.py
------------------------------
A provider's recurring hours, their one-off blocked dates, and the public
slot lookup built from both.

Everything here is written straight into the table that calendar_logic reads
on every booking, so this is where the times have to be checked. A row that
gets past this point is one the slot engine has to cope with forever.
"""

import re

from flask import Blueprint, g, jsonify, request

import database as db
from auth import roles_required, token_required
from calendar_logic import get_free_slots

bp = Blueprint("availability_routes", __name__, url_prefix="/api")

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
YYYY_MM_DD = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A slot has to be long enough to be a real appointment and short enough to
# fit in a day. Zero is the one that matters: calendar_logic walks a window
# in slot_minutes steps, so a zero step never terminates -- it filled memory
# and hung the worker, and any provider could set it on their own calendar.
MIN_SLOT_MINUTES = 5
MAX_SLOT_MINUTES = 24 * 60

DAYS_IN_WEEK = 7
DEFAULT_SLOT_MINUTES = 30


@bp.get("/providers/<int:provider_id>/slots")
@token_required
def free_slots(provider_id):
    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"error": "Query param 'date' (YYYY-MM-DD) is required"}), 400
    try:
        slots = get_free_slots(provider_id, date_str)
    except ValueError:
        return jsonify({"error": "Date must be in YYYY-MM-DD format"}), 400
    return jsonify({"provider_id": provider_id, "date": date_str, "slots": slots})


@bp.get("/availability/mine")
@token_required
@roles_required("provider")
def my_availability():
    windows = db.query(
        "SELECT * FROM availability WHERE provider_id = ? ORDER BY day_of_week, start_time",
        (g.current_user["id"],),
    )
    blocks = db.query(
        "SELECT * FROM blocked_slots WHERE provider_id = ? ORDER BY date",
        (g.current_user["id"],),
    )
    return jsonify({"windows": windows, "blocks": blocks})


@bp.post("/availability/mine")
@token_required
@roles_required("provider")
def add_availability_window():
    data = request.get_json(silent=True) or {}
    try:
        day_of_week = int(data["day_of_week"])
        start_time = data["start_time"]
        end_time = data["end_time"]
        slot_minutes = int(data.get("slot_minutes", DEFAULT_SLOT_MINUTES))
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "day_of_week, start_time, end_time are required"}), 400

    problem = _window_problem(day_of_week, start_time, end_time, slot_minutes)
    if problem:
        return jsonify({"error": problem}), 400

    new_id = db.execute(
        "INSERT INTO availability (provider_id, day_of_week, start_time, end_time, slot_minutes) "
        "VALUES (?, ?, ?, ?, ?)",
        (g.current_user["id"], day_of_week, start_time, end_time, slot_minutes),
    )
    return jsonify({"id": new_id}), 201


def _window_problem(day_of_week, start_time, end_time, slot_minutes):
    """The first thing wrong with a weekly window, or None.

    The format checks are the load-bearing ones. calendar_logic parses these
    strings with str.split(":") on every slot lookup, so anything that is not
    HH:MM reaches it as a ValueError and comes back as a 500 from the booking
    endpoint -- stored once by the provider, hit by every client afterwards.
    """
    if not (0 <= day_of_week <= DAYS_IN_WEEK - 1):
        return "day_of_week must be 0 (Sun) through 6 (Sat)"
    if not HHMM.match(start_time or "") or not HHMM.match(end_time or ""):
        return "start_time and end_time must look like HH:MM, e.g. 09:00"
    if start_time >= end_time:
        return "start_time must be before end_time"
    if not (MIN_SLOT_MINUTES <= slot_minutes <= MAX_SLOT_MINUTES):
        return (f"slot_minutes must be between {MIN_SLOT_MINUTES} and "
                f"{MAX_SLOT_MINUTES}")
    return None


@bp.delete("/availability/mine/<int:window_id>")
@token_required
@roles_required("provider")
def delete_availability_window(window_id):
    row = db.query(
        "SELECT id FROM availability WHERE id = ? AND provider_id = ?",
        (window_id, g.current_user["id"]),
        one=True,
    )
    if not row:
        return jsonify({"error": "Availability window not found"}), 404
    db.execute("DELETE FROM availability WHERE id = ?", (window_id,))
    return jsonify({"deleted": True})


@bp.post("/availability/mine/block")
@token_required
@roles_required("provider")
def add_block():
    """Block a whole date, or a range within one.

    Leave start_time and end_time out and the whole day goes. Supply them and
    they have to be a real pair -- a start with no end used to be accepted,
    and then every slot lookup and every booking attempt for that provider on
    that date returned a 500.
    """
    data = request.get_json(silent=True) or {}
    date_str = data.get("date")
    start_time = data.get("start_time") or None
    end_time = data.get("end_time") or None

    problem = _block_problem(date_str, start_time, end_time)
    if problem:
        return jsonify({"error": problem}), 400

    new_id = db.execute(
        "INSERT INTO blocked_slots (provider_id, date, start_time, end_time, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (g.current_user["id"], date_str, start_time, end_time, data.get("reason")),
    )
    return jsonify({"id": new_id}), 201


def _block_problem(date_str, start_time, end_time):
    """The first thing wrong with a blocked date, or None."""
    if not date_str:
        return "date (YYYY-MM-DD) is required"
    if not YYYY_MM_DD.match(date_str):
        return "date must be in YYYY-MM-DD format"
    if start_time is None and end_time is None:
        return None                       # a whole day off
    if start_time is None or end_time is None:
        return "give both start_time and end_time, or neither for the whole day"
    if not HHMM.match(start_time) or not HHMM.match(end_time):
        return "start_time and end_time must look like HH:MM, e.g. 09:00"
    if start_time >= end_time:
        return "start_time must be before end_time"
    return None


@bp.delete("/availability/mine/block/<int:block_id>")
@token_required
@roles_required("provider")
def delete_block(block_id):
    row = db.query(
        "SELECT id FROM blocked_slots WHERE id = ? AND provider_id = ?",
        (block_id, g.current_user["id"]),
        one=True,
    )
    if not row:
        return jsonify({"error": "Block not found"}), 404
    db.execute("DELETE FROM blocked_slots WHERE id = ?", (block_id,))
    return jsonify({"deleted": True})
