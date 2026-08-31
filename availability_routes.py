from flask import Blueprint, g, jsonify, request

import database as db
from auth import roles_required, token_required
from calendar_logic import get_free_slots

bp = Blueprint("availability_routes", __name__, url_prefix="/api")


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
        slot_minutes = int(data.get("slot_minutes", 30))
    except (KeyError, ValueError, TypeError):
        return jsonify({"error": "day_of_week, start_time, end_time are required"}), 400

    if not (0 <= day_of_week <= 6):
        return jsonify({"error": "day_of_week must be 0 (Sun) through 6 (Sat)"}), 400
    if start_time >= end_time:
        return jsonify({"error": "start_time must be before end_time"}), 400

    new_id = db.execute(
        "INSERT INTO availability (provider_id, day_of_week, start_time, end_time, slot_minutes) "
        "VALUES (?, ?, ?, ?, ?)",
        (g.current_user["id"], day_of_week, start_time, end_time, slot_minutes),
    )
    return jsonify({"id": new_id}), 201


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
    data = request.get_json(silent=True) or {}
    date_str = data.get("date")
    if not date_str:
        return jsonify({"error": "date (YYYY-MM-DD) is required"}), 400
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    reason = data.get("reason")

    new_id = db.execute(
        "INSERT INTO blocked_slots (provider_id, date, start_time, end_time, reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (g.current_user["id"], date_str, start_time, end_time, reason),
    )
    return jsonify({"id": new_id}), 201


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
