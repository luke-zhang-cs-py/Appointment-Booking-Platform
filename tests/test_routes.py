"""The HTTP surface: who is allowed to call what, and what a guest is
allowed to see."""

import datetime


def a_weekday():
    return (datetime.date.today() + datetime.timedelta(days=5)).isoformat()


# ---------------------------------------------------------------- offerings

def test_public_catalogue_needs_no_login(client, provider, offering):
    res = client.get(f"/api/providers/{provider['id']}/offerings")
    assert res.status_code == 200
    body = res.get_json()
    assert body["count"] == 1
    assert body["groups"][0]["offerings"][0]["price"] == "$90 CAD"


def test_public_catalogue_price_range(client, provider):
    for cents in (0, 4000, 15000):
        client.post("/api/offerings/mine",
                    json={"title": f"S{cents}", "durationMin": 30, "priceCents": cents},
                    headers=provider["auth"])
    body = client.get(f"/api/providers/{provider['id']}/offerings").get_json()
    assert body["priceRange"]["hasFree"] is True
    assert "free" in body["priceRange"]["label"].lower()


def test_unknown_provider_is_404(client):
    assert client.get("/api/providers/9999/offerings").status_code == 404


def test_a_client_is_not_a_provider(client, provider):
    """Role gating: a client must not be able to list a catalogue as if it
    were theirs."""
    from tests.conftest import register
    token, _ = register(client, "justaclient@test.local")
    res = client.get("/api/offerings/mine",
                     headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_offering_endpoints_need_auth(client):
    assert client.get("/api/offerings/mine").status_code == 401
    assert client.post("/api/offerings/mine", json={"title": "x"}).status_code == 401


def test_create_update_deactivate_round_trip(client, provider):
    created = client.post("/api/offerings/mine",
                          json={"title": "Round trip", "durationMin": 45,
                                "priceCents": 5500, "category": "Careers"},
                          headers=provider["auth"])
    assert created.status_code == 201
    oid = created.get_json()["offering"]["id"]

    patched = client.patch(f"/api/offerings/mine/{oid}", json={"priceCents": 6000},
                           headers=provider["auth"])
    assert patched.get_json()["offering"]["priceCents"] == 6000

    assert client.delete(f"/api/offerings/mine/{oid}",
                         headers=provider["auth"]).status_code == 200
    listed = client.get(f"/api/providers/{provider['id']}/offerings").get_json()
    assert all(o["id"] != oid for g in listed["groups"] for o in g["offerings"])


def test_non_numeric_price_is_rejected(client, provider):
    res = client.post("/api/offerings/mine",
                      json={"title": "Bad", "priceCents": "lots"},
                      headers=provider["auth"])
    assert res.status_code == 400


# ------------------------------------------------------------ coffee chats

def test_invite_requires_provider_role(client):
    from tests.conftest import register
    token, _ = register(client, "clientish@test.local")
    res = client.post("/api/coffee/invites", json={"email": "x@y.com"},
                      headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403


def test_invite_and_list(client, provider):
    res = client.post("/api/coffee/invites",
                      json={"email": "list@test.local", "name": "Listy"},
                      headers=provider["auth"])
    assert res.status_code == 201
    body = client.get("/api/coffee/invites", headers=provider["auth"]).get_json()
    assert body["stats"]["total"] == 1


def test_guest_page_needs_no_auth_and_hides_internals(client, provider, offering):
    made = client.post("/api/coffee/invites",
                       json={"email": "guest@test.local", "offeringId": offering["id"]},
                       headers=provider["auth"]).get_json()["invite"]

    res = client.get(f"/api/coffee/public/{made['token']}")
    assert res.status_code == 200
    body = res.get_json()

    # A leaked link should cost one coffee chat, not read access to a calendar.
    for leaked in ("token", "host_id", "id", "nudge_count", "guest_id"):
        assert leaked not in body, f"{leaked} must not reach the guest"
    assert body["hostName"] == "Test Provider"
    assert body["offering"]["price"] == "$90 CAD"
    assert body["days"], "free slots offered"


def test_opening_the_page_marks_it_viewed(client, provider):
    made = client.post("/api/coffee/invites", json={"email": "seen@test.local"},
                       headers=provider["auth"]).get_json()["invite"]
    client.get(f"/api/coffee/public/{made['token']}")
    listed = client.get("/api/coffee/invites", headers=provider["auth"]).get_json()
    assert listed["invites"][0]["status"] == "viewed"


def test_bogus_token_is_404(client):
    assert client.get("/api/coffee/public/nope").status_code == 404
    assert client.post("/api/coffee/public/nope/book", json={}).status_code == 400


def test_guest_books_and_gets_a_confirmation(client, provider, offering):
    made = client.post("/api/coffee/invites",
                       json={"email": "booker@test.local", "offeringId": offering["id"]},
                       headers=provider["auth"]).get_json()["invite"]
    page = client.get(f"/api/coffee/public/{made['token']}").get_json()
    day = page["days"][0]

    res = client.post(f"/api/coffee/public/{made['token']}/book",
                      json={"date": day["date"], "time": day["slots"][0]["start"],
                            "name": "Booker"})
    assert res.status_code == 201
    assert res.get_json()["booked"] is True


def test_double_booking_the_same_slot_is_409(client, provider):
    a = client.post("/api/coffee/invites", json={"email": "one@test.local"},
                    headers=provider["auth"]).get_json()["invite"]
    b = client.post("/api/coffee/invites", json={"email": "two@test.local"},
                    headers=provider["auth"]).get_json()["invite"]
    page = client.get(f"/api/coffee/public/{a['token']}").get_json()
    day = page["days"][0]
    slot = day["slots"][0]["start"]

    assert client.post(f"/api/coffee/public/{a['token']}/book",
                       json={"date": day["date"], "time": slot}).status_code == 201
    clash = client.post(f"/api/coffee/public/{b['token']}/book",
                        json={"date": day["date"], "time": slot})
    assert clash.status_code == 409, "a conflict the guest can resolve, not a bad request"


def test_decline_from_the_link(client, provider):
    made = client.post("/api/coffee/invites", json={"email": "no@test.local"},
                       headers=provider["auth"]).get_json()["invite"]
    res = client.post(f"/api/coffee/public/{made['token']}/decline",
                      json={"reason": "Bad timing"})
    assert res.status_code == 200
    listed = client.get("/api/coffee/invites", headers=provider["auth"]).get_json()
    assert listed["invites"][0]["status"] == "declined"


def test_nudge_and_revoke(client, provider):
    made = client.post("/api/coffee/invites", json={"email": "nudge@test.local"},
                       headers=provider["auth"]).get_json()["invite"]
    nudged = client.post(f"/api/coffee/invites/{made['id']}/nudge",
                         headers=provider["auth"])
    assert nudged.status_code == 200
    assert nudged.get_json()["invite"]["nudgeCount"] == 1

    assert client.delete(f"/api/coffee/invites/{made['id']}",
                         headers=provider["auth"]).status_code == 200
    page = client.get(f"/api/coffee/public/{made['token']}").get_json()
    assert page["open"] is False and page["days"] == []


def test_cannot_nudge_another_hosts_invite(client, provider):
    from tests.conftest import register
    made = client.post("/api/coffee/invites", json={"email": "mine@test.local"},
                       headers=provider["auth"]).get_json()["invite"]
    other_token, _ = register(client, "rival@test.local", role="provider")
    res = client.post(f"/api/coffee/invites/{made['id']}/nudge",
                      headers={"Authorization": f"Bearer {other_token}"})
    assert res.status_code == 404


def test_run_nudges_is_admin_only(client, provider):
    assert client.post("/api/coffee/run-nudges",
                       headers=provider["auth"]).status_code == 403


# ------------------------------------------------------------------- pages

def test_pages_render(client, provider):
    made = client.post("/api/coffee/invites", json={"email": "page@test.local"},
                       headers=provider["auth"]).get_json()["invite"]
    assert client.get("/").status_code == 200
    guest = client.get(f"/coffee/{made['token']}")
    assert guest.status_code == 200
    assert b"api/coffee/public" in guest.data
