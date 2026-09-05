"""The API speaks one language.

It used to speak two, decided by which handler you happened to hit. Guest and
catalogue endpoints returned camelCase from hand-built dicts; appointments,
availability, users and the email log returned `dict(row)` straight out of
SQLite. The same client got `guestEmail` from one endpoint and `start_time`
from the next, and which one you got was an accident of how that handler had
been written.

This is the test that makes it stay one language: it walks every response and
fails on any key that is not camelCase, so a new endpoint returning a raw row
is caught by the suite rather than by whoever writes the frontend.
"""

import re

import pytest

SNAKE = re.compile(r"[a-z0-9]_[a-z]")


def snake_keys(value, path=""):
    """Every snake_case key anywhere in a response, with where it was."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else key
            if SNAKE.search(key):
                found.append(here)
            found += snake_keys(item, here)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += snake_keys(item, f"{path}[{i}]")
    return found


def assert_camel(response, label):
    assert response.status_code < 400, f"{label} -> {response.status_code}"
    offenders = snake_keys(response.get_json())
    assert not offenders, f"{label} returns snake_case: {offenders}"


# ------------------------------------------------------- the whole surface

def test_every_authenticated_endpoint_answers_in_camel(client, provider, booking,
                                                       admin, offering):
    """The four that were raw rows are the point, but sweeping all of them is
    what stops the next one drifting."""
    checks = [
        ("GET /api/appointments/mine (client)", client.get(
            "/api/appointments/mine", headers=booking["auth"])),
        ("GET /api/appointments/mine (provider)", client.get(
            "/api/appointments/mine", headers=provider["auth"])),
        ("GET /api/appointments/mine (admin)", client.get(
            "/api/appointments/mine", headers=admin["auth"])),
        ("GET /api/availability/mine", client.get(
            "/api/availability/mine", headers=provider["auth"])),
        ("GET /api/providers", client.get("/api/providers", headers=booking["auth"])),
        ("GET /api/admin/users", client.get("/api/admin/users", headers=admin["auth"])),
        ("GET /api/admin/emails", client.get("/api/admin/emails", headers=admin["auth"])),
        ("GET /api/offerings/mine", client.get(
            "/api/offerings/mine", headers=provider["auth"])),
        ("GET /api/coffee/invites", client.get(
            "/api/coffee/invites", headers=provider["auth"])),
        ("GET /api/providers/<id>/offerings", client.get(
            f"/api/providers/{provider['id']}/offerings")),
    ]
    for label, response in checks:
        assert_camel(response, label)


def test_the_guest_endpoints_answer_in_camel(client, provider, offering):
    made = client.post("/api/coffee/invites",
                       json={"email": "camel@test.local", "offeringId": offering["id"]},
                       headers=provider["auth"]).get_json()["invite"]
    assert_camel(client.get(f"/api/coffee/public/{made['token']}"), "guest page")

    page = client.get(f"/api/coffee/public/{made['token']}").get_json()
    day = page["days"][0]
    booked = client.post(f"/api/coffee/public/{made['token']}/book",
                         json={"date": day["date"], "time": day["slots"][0]["start"]})
    assert_camel(booked, "guest booking")


def test_a_created_appointment_comes_back_in_camel(client, provider, booking):
    """The 201 body from a booking was a raw row."""
    day = booking["date"]
    free = client.get(f"/api/providers/{provider['id']}/slots?date={day}",
                      headers=booking["auth"]).get_json()["slots"]
    spare = [s for s in free if s["start"] != booking["start"]][0]
    res = client.post("/api/appointments", headers=booking["auth"], json={
        "provider_id": provider["id"], "date": day,
        "start_time": spare["start"], "end_time": spare["end"]})
    assert_camel(res, "POST /api/appointments")
    assert res.get_json()["appointment"]["startTime"] == spare["start"]


def test_the_slot_lookup_answers_in_camel(client, provider, booking):
    assert_camel(client.get(f"/api/providers/{provider['id']}/slots?date={booking['date']}",
                            headers=booking["auth"]), "slots")


# --------------------------------------------------------------- the helper

@pytest.mark.parametrize("given,expected", [
    ("start_time", "startTime"),
    ("provider_id", "providerId"),
    ("reminder_hours_before", "reminderHoursBefore"),
    ("id", "id"),
    ("status", "status"),
    ("alreadyCamel", "alreadyCamel"),
    ("", ""),
])
def test_camel_converts_names_and_leaves_the_rest(given, expected):
    from routes import camel
    assert camel(given) == expected


def test_camel_keys_renames_keys_and_never_values():
    """A `status` of "confirmed" is data. Rewriting a value because it looks
    like an identifier is a bug that only shows up for the one record whose
    content happens to contain an underscore."""
    from routes import camel_keys
    out = camel_keys({"start_time": "09:00", "note": "call_me_back",
                      "nested": [{"end_time": "10:00"}]})
    assert out == {"startTime": "09:00", "note": "call_me_back",
                   "nested": [{"endTime": "10:00"}]}


def test_camel_keys_leaves_scalars_alone():
    from routes import camel_keys
    for value in (1, "a_b", None, True, 2.5):
        assert camel_keys(value) == value
