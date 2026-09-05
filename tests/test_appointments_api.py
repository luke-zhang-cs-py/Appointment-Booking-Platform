"""The original booking API and the schedule behind it.

These predate the coffee chat work and were the least covered part of the
project: 46% and 53%. They are also where the permission checks live, and a
permission check with no test is a permission check nobody has run.
"""

import datetime

import pytest


def a_weekday():
    return (datetime.date.today() + datetime.timedelta(days=6)).isoformat()


def slots_for(client, provider, auth, day=None):
    day = day or a_weekday()
    res = client.get(f"/api/providers/{provider['id']}/slots?date={day}", headers=auth)
    return day, res.get_json()["slots"]


# ----------------------------------------------------------------- booking

def test_booking_a_free_slot(client, provider):
    from tests.conftest import register
    token, _ = register(client, "wants@test.local")
    auth = {"Authorization": f"Bearer {token}"}
    day, slots = slots_for(client, provider, auth)

    res = client.post("/api/appointments", headers=auth, json={
        "provider_id": provider["id"], "date": day,
        "start_time": slots[0]["start"], "end_time": slots[0]["end"]})
    assert res.status_code == 201
    assert res.get_json()["appointment"]["status"] == "confirmed"


def test_a_provider_cannot_book_for_themselves(client, provider):
    """Role gating, and the reason it is not just tidiness: a provider
    booking their own calendar is how you get an appointment with nobody."""
    res = client.post("/api/appointments", headers=provider["auth"], json={
        "provider_id": provider["id"], "date": a_weekday(),
        "start_time": "09:00", "end_time": "09:15"})
    assert res.status_code == 403


@pytest.mark.parametrize("body", [
    {}, {"provider_id": "not a number", "date": "x", "start_time": "y", "end_time": "z"},
    {"provider_id": 1, "date": "2026-01-01"},
])
def test_incomplete_booking_requests_are_400(client, booking, body):
    assert client.post("/api/appointments", headers=booking["auth"],
                       json=body).status_code == 400


def test_booking_an_unknown_provider_is_404(client, booking):
    res = client.post("/api/appointments", headers=booking["auth"], json={
        "provider_id": 9999, "date": a_weekday(),
        "start_time": "09:00", "end_time": "09:15"})
    assert res.status_code == 404


def test_booking_a_taken_slot_is_409(client, provider, booking):
    """409 rather than 400: the guest can fix it by picking again."""
    res = client.post("/api/appointments", headers=booking["auth"], json={
        "provider_id": provider["id"], "date": booking["date"],
        "start_time": booking["start"], "end_time": booking["end"]})
    assert res.status_code == 409


def test_booking_outside_the_working_day_is_409(client, provider, booking):
    res = client.post("/api/appointments", headers=booking["auth"], json={
        "provider_id": provider["id"], "date": booking["date"],
        "start_time": "03:00", "end_time": "03:15"})
    assert res.status_code == 409


# ------------------------------------------------------------------ my list

def test_each_role_sees_its_own_side(client, provider, booking, admin):
    mine = client.get("/api/appointments/mine", headers=booking["auth"]).get_json()
    assert len(mine["appointments"]) == 1
    assert mine["appointments"][0]["providerName"] == "Test Provider"

    theirs = client.get("/api/appointments/mine", headers=provider["auth"]).get_json()
    assert theirs["appointments"][0]["clientName"] == "Bo Oker"

    everything = client.get("/api/appointments/mine", headers=admin["auth"]).get_json()
    assert len(everything["appointments"]) == 1
    assert "providerName" in everything["appointments"][0]


def test_appointments_need_a_login(client):
    assert client.get("/api/appointments/mine").status_code == 401
    assert client.post("/api/appointments", json={}).status_code == 401


def test_a_junk_token_is_401(client):
    res = client.get("/api/appointments/mine",
                     headers={"Authorization": "Bearer not-a-token"})
    assert res.status_code == 401


def test_a_header_without_bearer_is_401(client, booking):
    res = client.get("/api/appointments/mine",
                     headers={"Authorization": booking["token"]})
    assert res.status_code == 401


# ------------------------------------------------------- cancel and complete

def test_either_party_can_cancel(client, provider, booking):
    assert client.post(f"/api/appointments/{booking['id']}/cancel",
                       headers=booking["auth"]).status_code == 200
    listed = client.get("/api/appointments/mine", headers=provider["auth"]).get_json()
    assert listed["appointments"][0]["status"] == "cancelled"


def test_a_stranger_cannot_cancel(client, booking):
    """The check that used to hinge on a function returning the string
    "forbidden", which callers compared a dict against."""
    from tests.conftest import register
    token, _ = register(client, "nosy@test.local")
    res = client.post(f"/api/appointments/{booking['id']}/cancel",
                      headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_an_admin_can_cancel_anything(client, booking, admin):
    assert client.post(f"/api/appointments/{booking['id']}/cancel",
                       headers=admin["auth"]).status_code == 200


def test_cancelling_twice_is_400(client, booking):
    client.post(f"/api/appointments/{booking['id']}/cancel", headers=booking["auth"])
    again = client.post(f"/api/appointments/{booking['id']}/cancel", headers=booking["auth"])
    assert again.status_code == 400
    assert "already cancelled" in again.get_json()["error"]


def test_cancelling_something_that_is_not_there_is_404(client, booking):
    assert client.post("/api/appointments/9999/cancel",
                       headers=booking["auth"]).status_code == 404


def test_only_the_provider_completes(client, provider, booking):
    assert client.post(f"/api/appointments/{booking['id']}/complete",
                       headers=booking["auth"]).status_code == 403
    assert client.post(f"/api/appointments/{booking['id']}/complete",
                       headers=provider["auth"]).status_code == 200


def test_a_cancelled_appointment_cannot_be_completed(client, provider, booking):
    client.post(f"/api/appointments/{booking['id']}/cancel", headers=booking["auth"])
    res = client.post(f"/api/appointments/{booking['id']}/complete", headers=provider["auth"])
    assert res.status_code == 400


def test_a_provider_cannot_complete_someone_elses(client, provider, booking):
    from tests.conftest import register
    token, _ = register(client, "rival@test.local", role="provider")
    res = client.post(f"/api/appointments/{booking['id']}/complete",
                      headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_cancelling_frees_the_slot_again(client, provider, booking):
    """The point of cancelling."""
    client.post(f"/api/appointments/{booking['id']}/cancel", headers=booking["auth"])
    _, slots = slots_for(client, provider, booking["auth"], booking["date"])
    assert any(s["start"] == booking["start"] for s in slots)


# ------------------------------------------------------------- availability

def test_slots_need_a_date(client, booking):
    res = client.get("/api/providers/1/slots", headers=booking["auth"])
    assert res.status_code == 400


def test_a_malformed_date_is_400_not_500(client, booking):
    res = client.get("/api/providers/1/slots?date=the-third-of-never",
                     headers=booking["auth"])
    assert res.status_code == 400


def test_a_provider_reads_back_their_own_schedule(client, provider):
    body = client.get("/api/availability/mine", headers=provider["auth"]).get_json()
    assert len(body["windows"]) == 7
    assert body["blocks"] == []


def test_only_providers_have_a_schedule(client, booking):
    assert client.get("/api/availability/mine", headers=booking["auth"]).status_code == 403


@pytest.mark.parametrize("body,why", [
    ({"day_of_week": 9, "start_time": "09:00", "end_time": "17:00"}, "no ninth day"),
    ({"day_of_week": 1, "start_time": "17:00", "end_time": "09:00"}, "ends before it starts"),
    ({"start_time": "09:00", "end_time": "17:00"}, "no day at all"),
    ({"day_of_week": "Tuesday", "start_time": "09:00", "end_time": "17:00"}, "not a number"),
])
def test_impossible_windows_are_refused(client, provider, body, why):
    res = client.post("/api/availability/mine", json=body, headers=provider["auth"])
    assert res.status_code == 400, why


def test_a_window_can_be_removed(client, provider):
    windows = client.get("/api/availability/mine",
                         headers=provider["auth"]).get_json()["windows"]
    target = windows[0]["id"]
    assert client.delete(f"/api/availability/mine/{target}",
                         headers=provider["auth"]).status_code == 200
    left = client.get("/api/availability/mine", headers=provider["auth"]).get_json()["windows"]
    assert all(w["id"] != target for w in left)


def test_removing_a_window_that_is_not_yours_is_404(client, provider):
    from tests.conftest import register
    windows = client.get("/api/availability/mine",
                         headers=provider["auth"]).get_json()["windows"]
    token, _ = register(client, "otherprov@test.local", role="provider")
    res = client.delete(f"/api/availability/mine/{windows[0]['id']}",
                        headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 404


# ------------------------------------------------------------------- blocks

def test_a_whole_day_block_clears_the_day(client, provider, booking):
    day = booking["date"]
    assert client.post("/api/availability/mine/block", json={"date": day, "reason": "Away"},
                       headers=provider["auth"]).status_code == 201
    _, slots = slots_for(client, provider, booking["auth"], day)
    assert slots == [], "a day off is a day off, booked or not"


def test_a_partial_block_removes_only_that_range(client, provider, booking):
    day = booking["date"]
    client.post("/api/availability/mine/block",
                json={"date": day, "start_time": "09:00", "end_time": "12:00"},
                headers=provider["auth"])
    _, slots = slots_for(client, provider, booking["auth"], day)
    assert slots, "the afternoon survives"
    assert all(s["start"] >= "12:00" for s in slots)


def test_a_block_needs_a_date(client, provider):
    assert client.post("/api/availability/mine/block", json={"reason": "Away"},
                       headers=provider["auth"]).status_code == 400


def test_a_block_can_be_lifted(client, provider, booking):
    day = booking["date"]
    made = client.post("/api/availability/mine/block", json={"date": day},
                       headers=provider["auth"]).get_json()["id"]
    assert client.delete(f"/api/availability/mine/block/{made}",
                         headers=provider["auth"]).status_code == 200
    _, slots = slots_for(client, provider, booking["auth"], day)
    assert slots


def test_lifting_someone_elses_block_is_404(client, provider, booking):
    from tests.conftest import register
    made = client.post("/api/availability/mine/block", json={"date": booking["date"]},
                       headers=provider["auth"]).get_json()["id"]
    token, _ = register(client, "prov3@test.local", role="provider")
    res = client.delete(f"/api/availability/mine/block/{made}",
                        headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 404
