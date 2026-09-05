import re

from flask import Blueprint, current_app, g, jsonify, request

import database as db
import notifications
from auth import roles_required, token_required
from routes import camel_keys

bp = Blueprint("email_routes", __name__, url_prefix="/api/admin/emails")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@bp.get("")
@token_required
@roles_required("admin")
def list_emails():
    """Delivery log -- what the platform mailed, to whom, and whether it landed."""
    status = request.args.get("status")
    try:
        limit = min(int(request.args.get("limit", "100")), 500)
    except ValueError:
        limit = 100

    sql = (
        "SELECT e.id, e.kind, e.recipient, e.subject, e.status, e.error, "
        "e.appointment_id, e.created_at, e.sent_at "
        "FROM email_log e"
    )
    params = []
    if status in ("queued", "sent", "failed"):
        sql += " WHERE e.status = ?"
        params.append(status)
    sql += " ORDER BY e.id DESC LIMIT ?"
    params.append(limit)

    rows = db.query(sql, tuple(params))
    return jsonify({
        "emails": camel_keys(rows),
        "transport": "smtp" if current_app.config["SMTP_HOST"] else "console",
        "enabled": current_app.config["MAIL_ENABLED"],
        "reminderHoursBefore": current_app.config["REMINDER_HOURS_BEFORE"],
    })


@bp.post("/test")
@token_required
@roles_required("admin")
def send_test_email():
    """Prove the SMTP settings work without waiting for a real booking."""
    data = request.get_json(silent=True) or {}
    to = (data.get("to") or g.current_user["email"]).strip().lower()
    if not EMAIL_RE.match(to):
        return jsonify({"error": "That email address doesn't look valid"}), 400

    if not notifications.send_test(to, g.current_user):
        return jsonify({"error": "Mail is switched off (MAIL_ENABLED=0)"}), 409
    return jsonify({"queued": True, "to": to})


@bp.post("/run-reminders")
@token_required
@roles_required("admin")
def run_reminders():
    """
    Trigger a reminder scan immediately.

    The background scheduler does this on a timer; this endpoint exists so you
    can drive it from cron instead (or just check it works).
    """
    queued = notifications.send_due_reminders()
    return jsonify({"queued": queued})
