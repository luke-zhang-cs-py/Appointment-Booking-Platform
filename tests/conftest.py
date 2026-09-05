"""Shared fixtures.

DATABASE_URL is read at import time by config.Config, and database.py caches
_IS_POSTGRES from it, so the environment has to be set before anything else
is imported. That is why this file does the setenv at module level rather
than inside a fixture.
"""

import datetime
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="almanac-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "test.db")
# 32+ bytes: shorter and PyJWT emits InsecureKeyLengthWarning on every
# encode, which buries anything else the suite has to say.
os.environ.setdefault("SECRET_KEY", "test-secret-long-enough-for-hs256-and-then-some")
os.environ.setdefault("SMTP_HOST", "")          # mail is logged, never sent
os.environ.setdefault("APP_BASE_URL", "http://localhost:5000")

import pytest  # noqa: E402


@pytest.fixture
def app():
    """A fresh application and an empty database for every test.

    The schema is dropped and rebuilt rather than the file being replaced,
    because config.DATABASE_URL is fixed at import time and cannot be pointed
    somewhere new per-test.
    """
    import app as app_module
    import database as db

    flask_app = app_module.app
    flask_app.config["TESTING"] = True

    with flask_app.app_context():
        conn = db.get_db()
        for table in ("coffee_invites", "offerings", "email_log",
                      "appointments", "blocked_slots", "availability", "users"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
        db.init_db()
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ctx(app):
    """An application context, for testing modules directly rather than
    through HTTP."""
    with app.app_context():
        yield


def register(client, email, password="pw12345678", role="client", name=None):
    res = client.post("/api/auth/register", json={
        "name": name or email.split("@")[0].title(),
        "email": email, "password": password, "role": role,
    })
    body = res.get_json()
    return body.get("token"), (body.get("user") or {})


@pytest.fixture
def provider(client):
    """A provider with a full week of 15-minute availability.

    Fifteen minutes because it divides every duration the catalogue uses; a
    30-minute grid makes 45-minute sessions unbookable, which is the bug
    test_calendar_logic pins down.
    """
    token, user = register(client, "provider@test.local", role="provider",
                           name="Test Provider")
    for day in range(7):
        client.post("/api/availability/mine",
                    json={"day_of_week": day, "start_time": "09:00",
                          "end_time": "17:00", "slot_minutes": 15},
                    headers={"Authorization": f"Bearer {token}"})
    return {"token": token, "user": user, "id": user["id"],
            "auth": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def offering(client, provider):
    res = client.post("/api/offerings/mine", json={
        "title": "Mock interview", "category": "Software engineering",
        "durationMin": 60, "priceCents": 9000, "level": "Any",
        "summary": "A real interview, then feedback.",
    }, headers=provider["auth"])
    return res.get_json()["offering"]


@pytest.fixture
def admin(client):
    """An admin. Not self-registerable on purpose, so one is promoted.

    The token issued at registration keeps working: token_required reloads
    the user row on every request and roles_required reads the role from
    that, not from the claim -- which is what you want when a role changes.
    """
    import database as db
    token, user = register(client, "admin@test.local", role="provider", name="Admin")
    db.execute("UPDATE users SET role = 'admin' WHERE id = ?", (user["id"],))
    return {"token": token, "id": user["id"],
            "auth": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def booking(client, provider):
    """A confirmed appointment between a fresh client and the provider."""
    token, user = register(client, "booker@test.local", name="Bo Oker")
    day = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    auth = {"Authorization": f"Bearer {token}"}
    slots = client.get(f"/api/providers/{provider['id']}/slots?date={day}",
                       headers=auth).get_json()["slots"]
    res = client.post("/api/appointments", headers=auth, json={
        "provider_id": provider["id"], "date": day,
        "start_time": slots[0]["start"], "end_time": slots[0]["end"],
        "notes": "Bring the portfolio.",
    })
    return {"id": res.get_json()["appointment"]["id"], "token": token,
            "user": user, "auth": auth, "date": day,
            "start": slots[0]["start"], "end": slots[0]["end"],
            "free": slots}
