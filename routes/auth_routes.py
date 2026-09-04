import re

from flask import Blueprint, g, jsonify, request

import database as db
import notifications
from auth import create_token, hash_password, token_required, verify_password

bp = Blueprint("auth_routes", __name__, url_prefix="/api/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_SELF_SIGNUP_ROLES = ("client", "provider")


@bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = data.get("role", "client")
    specialty = (data.get("specialty") or "").strip() or None

    if not name or not email or not password:
        return jsonify({"error": "Name, email, and password are all required"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "That email address doesn't look valid"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password needs to be at least 8 characters"}), 400
    if role not in ALLOWED_SELF_SIGNUP_ROLES:
        return jsonify({"error": "Role must be 'client' or 'provider'"}), 400

    existing = db.query("SELECT id FROM users WHERE email = ?", (email,), one=True)
    if existing:
        return jsonify({"error": "An account with that email already exists"}), 409

    user_id = db.insert(
        "INSERT INTO users (name, email, password_hash, role, specialty) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, email, hash_password(password), role, specialty),
    )
    user = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    notifications.send_welcome(user)
    token = create_token(user)
    return jsonify({"token": token, "user": _public(user)}), 201


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    user = db.query("SELECT * FROM users WHERE email = ?", (email,), one=True)
    if not user or not verify_password(password, user["password_hash"]):
        return jsonify({"error": "Incorrect email or password"}), 401
    if not user.get("is_active", 1):
        return jsonify({"error": "This account has been deactivated"}), 403

    token = create_token(user)
    return jsonify({"token": token, "user": _public(user)})


@bp.get("/me")
@token_required
def me():
    return jsonify({"user": _public(g.current_user)})


def _public(user: dict) -> dict:
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "specialty": user.get("specialty"),
    }
