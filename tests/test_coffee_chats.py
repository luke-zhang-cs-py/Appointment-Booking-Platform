"""The invite lifecycle, and the guest booking path that has no account
behind it."""

import datetime

import pytest


def a_weekday():
    return (datetime.date.today() + datetime.timedelta(days=5)).isoformat()


# ---------------------------------------------------------------- creating

def test_create_and_fetch(ctx, provider):
    import coffee_chats as cc
    inv = cc.create_invite(provider["id"], "guest@test.local", guest_name="Guest")
    assert inv["status"] == "sent"
    assert cc.get_by_token(inv["token"])["id"] == inv["id"]


def test_email_is_normalised(ctx, provider):
    import coffee_chats as cc
    inv = cc.create_invite(provider["id"], "  MiXeD@Test.Local  ")
    assert inv["guest_email"] == "mixed@test.local"


@pytest.mark.parametrize("bad", ["", "   ", "nope", "@nope.com", "nope@"])
def test_bad_email_is_refused(ctx, provider, bad):
    import coffee_chats as cc
    with pytest.raises(cc.InviteError):
        cc.create_invite(provider["id"], bad)


def test_tokens_are_unique_and_long(ctx, provider):
    """The token is the only credential on the guest side."""
    import coffee_chats as cc
    tokens = {cc.create_invite(provider["id"], f"g{i}@test.local")["token"]
              for i in range(5)}
    assert len(tokens) == 5
    assert all(len(t) >= 32 for t in tokens)


def test_a_second_open_invite_to_the_same_person_is_refused(ctx, provider):
    """Almost always a double-send, and two links fragments the thread."""
    import coffee_chats as cc
    cc.create_invite(provider["id"], "dup@test.local")
    with pytest.raises(cc.InviteError):
        cc.create_invite(provider["id"], "dup@test.local")


def test_a_new_invite_is_allowed_once_the_old_one_closed(ctx, provider):
    import coffee_chats as cc
    first = cc.create_invite(provider["id"], "again@test.local")
    cc.revoke(first["id"], provider["id"])
    assert cc.create_invite(provider["id"], "again@test.local")


def test_an_offering_sets_the_duration_and_topic(ctx, provider, offering):
    import coffee_chats as cc
    inv = cc.create_invite(provider["id"], "off@test.local",
                           offering_id=offering["id"])
    assert inv["duration_min"] == offering["durationMin"]
    assert inv["topic"] == offering["title"]


def test_cannot_use_another_providers_offering(ctx, provider, offering, client):
    import coffee_chats as cc
    from tests.conftest import register
    _, other = register(client, "thief@test.local", role="provider")
    with pytest.raises(cc.InviteError):
        cc.create_invite(other["id"], "x@test.local", offering_id=offering["id"])


# ------------------------------------------------------------------- state

def test_viewing_is_recorded_once(ctx, provider):
    import coffee_chats as cc
    inv = cc.create_invite(provider["id"], "v@test.local")
    seen = cc.mark_viewed(inv)
    assert seen["status"] == "viewed" and seen["viewed_at"]
    again = cc.mark_viewed(seen)
    assert again["viewed_at"] == seen["viewed_at"], "first view only"


def test_expired_invites_are_closed_and_not_open(ctx, provider):
    import coffee_chats as cc
    import database as db
    inv = cc.create_invite(provider["id"], "old@test.local")
    db.execute("UPDATE coffee_invites SET expires_at = ? WHERE id = ?",
               ("2020-01-01T00:00:00", inv["id"]))
    assert not cc.is_open(cc.get_invite(inv["id"]))
    assert cc.expire_stale() == 1
    assert cc.get_invite(inv["id"])["status"] == "expired"


def test_revoke_refuses_a_booked_invite(ctx, provider):
    import coffee_chats as cc
    import database as db
    inv = cc.create_invite(provider["id"], "booked@test.local")
    db.execute("UPDATE coffee_invites SET status = 'booked' WHERE id = ?", (inv["id"],))
    with pytest.raises(cc.InviteError):
        cc.revoke(inv["id"], provider["id"])


def test_revoke_refuses_someone_elses_invite(ctx, provider, client):
    import coffee_chats as cc
    from tests.conftest import register
    _, other = register(client, "nosy@test.local", role="provider")
    inv = cc.create_invite(provider["id"], "mine@test.local")
    with pytest.raises(cc.InviteError):
        cc.revoke(inv["id"], other["id"])


# ------------------------------------------------------------------ nudges

def test_a_fresh_invite_is_not_due_for_a_nudge(ctx, provider):
    import coffee_chats as cc
    cc.create_invite(provider["id"], "fresh@test.local")
    assert cc.due_for_nudge() == []


def test_a_quiet_invite_becomes_due(ctx, provider):
    import coffee_chats as cc
    import database as db
    inv = cc.create_invite(provider["id"], "quiet@test.local")
    old = (datetime.datetime.now()
           - datetime.timedelta(days=cc.NUDGE_AFTER_DAYS + 1)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("UPDATE coffee_invites SET created_at = ? WHERE id = ?", (old, inv["id"]))
    assert [i["id"] for i in cc.due_for_nudge()] == [inv["id"]]


def test_nudging_resets_the_clock_and_caps_out(ctx, provider):
    """Measured from last contact, so a nudged invite waits the full interval
    again rather than being chased daily -- and stops at MAX_NUDGES."""
    import coffee_chats as cc
    import database as db
    inv = cc.create_invite(provider["id"], "capped@test.local")
    old = (datetime.datetime.now()
           - datetime.timedelta(days=cc.NUDGE_AFTER_DAYS + 1)).strftime("%Y-%m-%dT%H:%M:%S")

    for _ in range(cc.MAX_NUDGES):
        db.execute("UPDATE coffee_invites SET created_at = ?, last_nudge_at = ? WHERE id = ?",
                   (old, old, inv["id"]))
        assert cc.due_for_nudge(), "should still be due"
        cc.record_nudge(inv["id"])

    db.execute("UPDATE coffee_invites SET last_nudge_at = ? WHERE id = ?", (old, inv["id"]))
    assert cc.due_for_nudge() == [], "capped after MAX_NUDGES"
    assert cc.get_invite(inv["id"])["nudge_count"] == cc.MAX_NUDGES


def test_an_expired_invite_is_never_nudged(ctx, provider):
    import coffee_chats as cc
    import database as db
    inv = cc.create_invite(provider["id"], "gone@test.local")
    old = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("UPDATE coffee_invites SET created_at = ?, expires_at = ? WHERE id = ?",
               (old, "2020-01-01T00:00:00", inv["id"]))
    assert cc.due_for_nudge() == []


# ----------------------------------------------------------------- booking

def test_booking_creates_a_real_appointment_and_a_guest(ctx, provider, offering):
    import coffee_chats as cc
    import database as db
    from calendar_logic import slot_starts_for

    inv = cc.create_invite(provider["id"], "newcomer@test.local",
                           offering_id=offering["id"])
    day = a_weekday()
    start = slot_starts_for(provider["id"], day, inv["duration_min"])[0]["start"]

    booked, appt_id = cc.book(inv["token"], day, start, guest_name="Newcomer")
    assert booked["status"] == "booked" and booked["appointment_id"] == appt_id

    appt = db.query("SELECT * FROM appointments WHERE id = ?", (appt_id,), one=True)
    assert appt["date"] == day and appt["start_time"] == start
    mins = ((int(appt["end_time"][:2]) * 60 + int(appt["end_time"][3:]))
            - (int(appt["start_time"][:2]) * 60 + int(appt["start_time"][3:])))
    assert mins == offering["durationMin"], "length comes from the offering"

    guest = db.query("SELECT * FROM users WHERE email = ?", ("newcomer@test.local",), one=True)
    assert guest and guest["role"] == "client"
    assert guest["password_hash"] == "!invite-only", "created unable to log in"


def test_an_existing_user_is_reused_not_duplicated(ctx, provider, client):
    import coffee_chats as cc
    import database as db
    from calendar_logic import slot_starts_for
    from tests.conftest import register

    register(client, "already@test.local")
    inv = cc.create_invite(provider["id"], "already@test.local")
    day = a_weekday()
    start = slot_starts_for(provider["id"], day, inv["duration_min"])[0]["start"]
    cc.book(inv["token"], day, start)

    rows = db.query("SELECT id FROM users WHERE email = ?", ("already@test.local",))
    assert len(rows) == 1


def test_a_token_books_only_once(ctx, provider):
    import coffee_chats as cc
    from calendar_logic import slot_starts_for
    inv = cc.create_invite(provider["id"], "once@test.local")
    day = a_weekday()
    starts = slot_starts_for(provider["id"], day, inv["duration_min"])
    cc.book(inv["token"], day, starts[0]["start"])
    with pytest.raises(cc.InviteError):
        cc.book(inv["token"], day, starts[1]["start"])


def test_booking_a_taken_slot_fails(ctx, provider):
    import coffee_chats as cc
    from calendar_logic import slot_starts_for
    day = a_weekday()
    first = cc.create_invite(provider["id"], "a@test.local")
    start = slot_starts_for(provider["id"], day, first["duration_min"])[0]["start"]
    cc.book(first["token"], day, start)

    second = cc.create_invite(provider["id"], "b@test.local")
    with pytest.raises(cc.InviteError, match="took that slot"):
        cc.book(second["token"], day, start)


def test_booking_in_the_past_fails(ctx, provider):
    import coffee_chats as cc
    inv = cc.create_invite(provider["id"], "past@test.local")
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    with pytest.raises(cc.InviteError, match="past"):
        cc.book(inv["token"], yesterday, "10:00")


@pytest.mark.parametrize("date_str,time_str", [
    ("not-a-date", "10:00"), ("2026-01-01", "nope"), (None, None),
])
def test_malformed_date_or_time_fails(ctx, provider, date_str, time_str):
    import coffee_chats as cc
    inv = cc.create_invite(provider["id"], "bad@test.local")
    with pytest.raises(cc.InviteError):
        cc.book(inv["token"], date_str, time_str)


def test_an_unknown_token_fails(ctx, provider):
    import coffee_chats as cc
    with pytest.raises(cc.InviteError):
        cc.book("not-a-real-token", a_weekday(), "10:00")


def test_decline_closes_it(ctx, provider):
    import coffee_chats as cc
    inv = cc.create_invite(provider["id"], "no@test.local")
    declined = cc.decline(inv["token"], "Swamped this month.")
    assert declined["status"] == "declined" and declined["responded_at"]
    with pytest.raises(cc.InviteError):
        cc.decline(inv["token"])


def test_available_slots_fit_the_session(ctx, provider, offering):
    """Offering somebody 16:45 for a 60-minute session is an invitation to
    hit an error."""
    import coffee_chats as cc
    inv = cc.create_invite(provider["id"], "fit@test.local", offering_id=offering["id"])
    for day in cc.available_slots(inv):
        for slot in day["slots"]:
            assert slot["end"] <= "17:00"


def test_stats_count_conversion(ctx, provider):
    import coffee_chats as cc
    from calendar_logic import slot_starts_for
    a = cc.create_invite(provider["id"], "s1@test.local")
    cc.create_invite(provider["id"], "s2@test.local")
    day = a_weekday()
    cc.book(a["token"], day, slot_starts_for(provider["id"], day, a["duration_min"])[0]["start"])

    stats = cc.stats_for_host(provider["id"])
    assert stats["total"] == 2
    assert stats["byStatus"]["booked"] == 1
    assert stats["bookedRate"] == 0.5
