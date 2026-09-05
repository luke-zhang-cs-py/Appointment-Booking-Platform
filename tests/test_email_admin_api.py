"""The admin view of the delivery log, and the two manual triggers.

This is the only place the platform shows what it has been sending. It is
also the only route that can be pointed at an arbitrary address, so the
address is checked before anything is queued.
"""

import pytest


def test_the_log_lists_what_was_sent(client, admin, booking):
    body = client.get("/api/admin/emails", headers=admin["auth"]).get_json()
    kinds = {e["kind"] for e in body["emails"]}
    assert {"welcome", "booked_client", "booked_provider"} <= kinds
    assert body["transport"] == "console", "no SMTP_HOST in tests"
    assert body["enabled"] is True


def test_the_log_can_be_filtered_by_status(client, admin, booking):
    import mailer
    assert mailer.wait_until_idle(5.0)
    body = client.get("/api/admin/emails?status=sent", headers=admin["auth"]).get_json()
    assert body["emails"] and all(e["status"] == "sent" for e in body["emails"])
    none = client.get("/api/admin/emails?status=failed", headers=admin["auth"]).get_json()
    assert none["emails"] == []


def test_an_unknown_status_filter_is_ignored_not_an_error(client, admin, booking):
    """A typo in a query string should show you everything, not nothing and
    not a 500."""
    body = client.get("/api/admin/emails?status=elsewhere", headers=admin["auth"]).get_json()
    assert body["emails"]


@pytest.mark.parametrize("limit,why", [
    ("2", "honoured"), ("9999", "clamped to 500"), ("lots", "falls back to 100"),
])
def test_the_limit_is_bounded_and_survives_junk(client, admin, booking, limit, why):
    res = client.get(f"/api/admin/emails?limit={limit}", headers=admin["auth"])
    assert res.status_code == 200, why
    if limit == "2":
        assert len(res.get_json()["emails"]) <= 2


def test_the_log_is_admin_only(client, provider, booking):
    assert client.get("/api/admin/emails").status_code == 401
    assert client.get("/api/admin/emails", headers=provider["auth"]).status_code == 403


# ------------------------------------------------------------- test message

def test_a_test_email_defaults_to_the_admin(client, admin):
    res = client.post("/api/admin/emails/test", json={}, headers=admin["auth"])
    assert res.status_code == 200
    assert res.get_json()["to"] == "admin@test.local"


def test_a_test_email_can_be_addressed(client, admin):
    res = client.post("/api/admin/emails/test", json={"to": "Someone@Test.Local"},
                      headers=admin["auth"])
    assert res.get_json()["to"] == "someone@test.local", "normalised"


@pytest.mark.parametrize("to", ["not-an-address", "@nowhere.com", "nobody@",
                                "two@@at.com", "spaces in@here.com"])
def test_an_unsendable_address_is_refused_before_queueing(client, admin, to):
    """An empty "to" is not in this list on purpose: that means "send it to
    me", which the test above covers."""
    res = client.post("/api/admin/emails/test", json={"to": to}, headers=admin["auth"])
    assert res.status_code == 400


def test_a_test_email_reports_mail_being_switched_off(client, app, admin):
    """409 rather than a cheerful "queued": the whole point of the button is
    to tell you the truth about your mail settings."""
    app.config["MAIL_ENABLED"] = False
    try:
        res = client.post("/api/admin/emails/test", json={}, headers=admin["auth"])
        assert res.status_code == 409
    finally:
        app.config["MAIL_ENABLED"] = True


def test_the_test_email_is_admin_only(client, provider):
    assert client.post("/api/admin/emails/test", json={},
                       headers=provider["auth"]).status_code == 403


# ------------------------------------------------------------ manual scans

def test_reminders_can_be_driven_from_outside(client, admin, booking):
    """This endpoint exists so a bigger deployment can use cron instead of
    the in-process timer."""
    res = client.post("/api/admin/emails/run-reminders", headers=admin["auth"])
    assert res.status_code == 200
    assert res.get_json()["queued"] == 0, "the fixture books five days out"


def test_the_reminder_trigger_is_admin_only(client, provider):
    assert client.post("/api/admin/emails/run-reminders",
                       headers=provider["auth"]).status_code == 403


def test_the_nudge_trigger_reports_what_it_did(client, admin):
    res = client.post("/api/coffee/run-nudges", headers=admin["auth"])
    assert res.status_code == 200
    assert res.get_json() == {"nudged": 0, "expired": 0}
