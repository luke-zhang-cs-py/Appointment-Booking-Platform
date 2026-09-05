from flask import Blueprint, g, jsonify, request

import database as db
from auth import roles_required, token_required
from routes import camel_keys

bp = Blueprint("user_routes", __name__, url_prefix="/api")


@bp.get("/providers")
@token_required
def list_providers():
    """Public-to-any-logged-in-user directory of active providers, for clients to browse."""
    rows = db.query(
        "SELECT id, name, specialty FROM users WHERE role = 'provider' AND is_active = 1 "
        "ORDER BY name"
    )
    return jsonify({"providers": camel_keys(rows)})


@bp.get("/admin/users")
@token_required
@roles_required("admin")
def admin_list_users():
    rows = db.query(
        "SELECT id, name, email, role, specialty, is_active, created_at FROM users "
        "ORDER BY created_at DESC"
    )
    return jsonify({"users": camel_keys(rows)})


@bp.patch("/admin/users/<int:user_id>")
@token_required
@roles_required("admin")
def admin_update_user(user_id):
    data = request.get_json(silent=True) or {}
    target = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if not target:
        return jsonify({"error": "User not found"}), 404

    if "is_active" in data:
        if target["id"] == g.current_user["id"]:
            return jsonify({"error": "You can't deactivate your own account"}), 400
        db.execute(
            "UPDATE users SET is_active = ? WHERE id = ?",
            (1 if data["is_active"] else 0, user_id),
        )
    if "role" in data and data["role"] in ("admin", "provider", "client"):
        db.execute("UPDATE users SET role = ? WHERE id = ?", (data["role"], user_id))

    updated = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    return jsonify(
        {
            "user": {
                "id": updated["id"],
                "name": updated["name"],
                "email": updated["email"],
                "role": updated["role"],
                "is_active": bool(updated["is_active"]),
            }
        }
    )
