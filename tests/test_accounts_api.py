"""Registration, login, and the admin surface.

The interesting cases are all refusals. Anything that lets somebody register
as an admin, log into a deactivated account, or lock the last admin out is
worth a test; the happy path is exercised by every other file here.
"""

import pytest


# ------------------------------------------------------------- registering

def test_registering_returns_a_usable_token(client):
    res = client.post("/api/auth/register", json={
        "name": "New Person", "email": "new@test.local", "password": "pw12345678"})
    assert res.status_code == 201
    body = res.get_json()
    assert body["user"]["role"] == "client"
    assert "password" not in body["user"] and "password_hash" not in body["user"]

    me = client.get("/api/auth/me",
                    headers={"Authorization": f"Bearer {body['token']}"})
    assert me.get_json()["user"]["email"] == "new@test.local"


@pytest.mark.parametrize("body,why", [
    ({"email": "a@b.co", "password": "pw12345678"}, "no name"),
    ({"name": "A", "password": "pw12345678"}, "no email"),
    ({"name": "A", "email": "a@b.co"}, "no password"),
    ({"name": "A", "email": "not-an-email", "password": "pw12345678"}, "bad email"),
    ({"name": "A", "email": "a@b.co", "password": "short"}, "password too short"),
])
def test_incomplete_registrations_are_refused(client, body, why):
    assert client.post("/api/auth/register", json=body).status_code == 400, why


def test_nobody_registers_themselves_as_an_admin(client):
    """The one role escalation that would matter. Admins are promoted, not
    self-declared."""
    res = client.post("/api/auth/register", json={
        "name": "Sneaky", "email": "sneaky@test.local",
        "password": "pw12345678", "role": "admin"})
    assert res.status_code == 400


def test_an_email_belongs_to_one_account(client):
    from tests.conftest import register
    register(client, "taken@test.local")
    again = client.post("/api/auth/register", json={
        "name": "Impostor", "email": "taken@test.local", "password": "pw12345678"})
    assert again.status_code == 409


def test_email_case_does_not_create_a_second_account(client):
    from tests.conftest import register
    register(client, "mixed@test.local")
    again = client.post("/api/auth/register", json={
        "name": "Same", "email": "MIXED@Test.Local", "password": "pw12345678"})
    assert again.status_code == 409


# ------------------------------------------------------------------ logging in

def test_login_round_trip(client):
    from tests.conftest import register
    register(client, "returning@test.local")
    res = client.post("/api/auth/login",
                      json={"email": "returning@test.local", "password": "pw12345678"})
    assert res.status_code == 200
    assert res.get_json()["user"]["email"] == "returning@test.local"


@pytest.mark.parametrize("body", [
    {"email": "returning@test.local", "password": "wrong-password"},
    {"email": "nobody@test.local", "password": "pw12345678"},
    {},
])
def test_bad_credentials_are_401_and_say_nothing_useful(client, body):
    """The same message either way: which half was wrong is an account
    enumeration hint."""
    from tests.conftest import register
    register(client, "returning@test.local")
    res = client.post("/api/auth/login", json=body)
    assert res.status_code == 401
    assert res.get_json()["error"] == "Incorrect email or password"


def test_a_deactivated_account_cannot_log_in(client, admin):
    from tests.conftest import register
    register(client, "gone@test.local")
    import database as db
    db.execute("UPDATE users SET is_active = 0 WHERE email = ?", ("gone@test.local",))
    res = client.post("/api/auth/login",
                      json={"email": "gone@test.local", "password": "pw12345678"})
    assert res.status_code == 403


def test_a_live_token_stops_working_once_the_account_is_off(client):
    """Deactivation has to bite immediately, not when the token expires --
    which is why token_required reloads the user on every request."""
    from tests.conftest import register
    import database as db
    token, user = register(client, "revoked@test.local")
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=auth).status_code == 200
    db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user["id"],))
    assert client.get("/api/auth/me", headers=auth).status_code == 401


def test_me_needs_a_token(client):
    assert client.get("/api/auth/me").status_code == 401


# ------------------------------------------------------------------- admin

def test_the_provider_directory_lists_only_active_providers(client, provider, booking):
    import database as db
    body = client.get("/api/providers", headers=booking["auth"]).get_json()
    assert [p["name"] for p in body["providers"]] == ["Test Provider"]

    db.execute("UPDATE users SET is_active = 0 WHERE id = ?", (provider["id"],))
    body = client.get("/api/providers", headers=booking["auth"]).get_json()
    assert body["providers"] == []


def test_the_directory_needs_a_login(client):
    assert client.get("/api/providers").status_code == 401


def test_only_an_admin_lists_every_account(client, provider, admin):
    assert client.get("/api/admin/users", headers=provider["auth"]).status_code == 403
    body = client.get("/api/admin/users", headers=admin["auth"]).get_json()
    assert len(body["users"]) == 2


def test_an_admin_can_change_a_role(client, booking, admin):
    res = client.patch(f"/api/admin/users/{booking['user']['id']}",
                       json={"role": "provider"}, headers=admin["auth"])
    assert res.status_code == 200
    assert res.get_json()["user"]["role"] == "provider"


def test_a_nonsense_role_is_ignored_not_applied(client, booking, admin):
    res = client.patch(f"/api/admin/users/{booking['user']['id']}",
                       json={"role": "superuser"}, headers=admin["auth"])
    assert res.get_json()["user"]["role"] == "client"


def test_an_admin_cannot_deactivate_themselves(client, admin):
    """Locking the only admin out of their own dashboard is a support ticket
    nobody can action."""
    res = client.patch(f"/api/admin/users/{admin['id']}",
                       json={"is_active": False}, headers=admin["auth"])
    assert res.status_code == 400


def test_patching_somebody_who_does_not_exist_is_404(client, admin):
    assert client.patch("/api/admin/users/9999", json={"role": "client"},
                        headers=admin["auth"]).status_code == 404
