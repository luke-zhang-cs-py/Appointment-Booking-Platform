import datetime
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

import database as db


def hash_password(raw_password: str) -> str:
    return generate_password_hash(raw_password)


def verify_password(raw_password: str, password_hash: str) -> bool:
    return check_password_hash(password_hash, raw_password)


def create_token(user: dict) -> str:
    now = datetime.datetime.utcnow()
    payload = {
        "sub": user["id"],
        "role": user["role"],
        "name": user["name"],
        "iat": now,
        "exp": now + datetime.timedelta(hours=current_app.config["JWT_EXP_HOURS"]),
    }
    return jwt.encode(payload, current_app.config["SECRET_KEY"], algorithm="HS256")


def decode_token(token: str):
    return jwt.decode(token, current_app.config["SECRET_KEY"], algorithms=["HS256"])


def _extract_token():
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:].strip()
    return None


def token_required(fn):
    """Attaches g.current_user = {id, role, name} or returns 401."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = _extract_token()
        if not token:
            return jsonify({"error": "Missing authorization token"}), 401
        try:
            payload = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired, please log in again"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        user = db.query("SELECT * FROM users WHERE id = ?", (payload["sub"],), one=True)
        if not user or not user.get("is_active", 1):
            return jsonify({"error": "Account not found or deactivated"}), 401

        g.current_user = user
        return fn(*args, **kwargs)

    return wrapper


def roles_required(*allowed_roles):
    """Stack under @token_required. Restricts endpoint to listed roles."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if g.current_user["role"] not in allowed_roles:
                return jsonify({"error": "You don't have permission to do that"}), 403
            return fn(*args, **kwargs)

        return wrapper

    return decorator
