"""calendar_logic decides what is bookable. Its exact-match bug made every
appointment longer than one slot impossible, so these pin that down."""

import datetime

import pytest


def a_weekday():
    """A date a few days out, so 'today' filtering never interferes."""
    return (datetime.date.today() + datetime.timedelta(days=5)).isoformat()


def test_free_slots_respect_the_window(ctx, provider):
    from calendar_logic import get_free_slots
    slots = get_free_slots(provider["id"], a_weekday())
    assert slots
    assert slots[0]["start"] == "09:00"
    assert slots[-1]["end"] == "17:00"


def test_free_slots_are_the_configured_size(ctx, provider):
    from calendar_logic import get_free_slots
    slots = get_free_slots(provider["id"], a_weekday())
    assert slots[0]["end"] == "09:15", "15-minute grid"


def test_no_availability_means_no_slots(ctx, client):
    from calendar_logic import get_free_slots
    from tests.conftest import register
    _, user = register(client, "empty@test.local", role="provider")
    assert get_free_slots(user["id"], a_weekday()) == []


def test_bad_date_raises(ctx, provider):
    from calendar_logic import get_free_slots
    with pytest.raises(ValueError):
        get_free_slots(provider["id"], "not-a-date")


def test_single_slot_booking_is_free(ctx, provider):
    from calendar_logic import is_slot_free
    assert is_slot_free(provider["id"], a_weekday(), "09:00", "09:15")


def test_a_booking_longer_than_one_slot_is_free(ctx, provider):
    """The regression. is_slot_free used to require an exact match against
    one generated slot, which made every multi-slot booking impossible and
    reported it as 'someone just took that slot'."""
    from calendar_logic import is_slot_free
    day = a_weekday()
    assert is_slot_free(provider["id"], day, "09:00", "10:00"), "60 min over a 15-min grid"
    assert is_slot_free(provider["id"], day, "09:00", "09:45"), "45 min"
    assert is_slot_free(provider["id"], day, "09:00", "10:30"), "90 min"


def test_a_booking_that_overruns_the_day_is_not_free(ctx, provider):
    from calendar_logic import is_slot_free
    assert not is_slot_free(provider["id"], a_weekday(), "16:30", "17:30")


def test_a_booking_off_the_grid_is_not_free(ctx, provider):
    from calendar_logic import is_slot_free
    assert not is_slot_free(provider["id"], a_weekday(), "09:07", "09:22")


def test_slot_starts_for_shrinks_as_the_session_lengthens(ctx, provider):
    from calendar_logic import slot_starts_for
    day = a_weekday()
    counts = {d: len(slot_starts_for(provider["id"], day, d))
              for d in (15, 30, 45, 60, 90)}
    assert counts[15] > counts[30] > counts[45] > counts[60] > counts[90]
    assert all(n > 0 for n in counts.values()), \
        "every catalogue duration must have somewhere to go on a 15-min grid"


def test_slot_starts_never_overrun_the_window(ctx, provider):
    from calendar_logic import slot_starts_for
    for start in slot_starts_for(provider["id"], a_weekday(), 90):
        assert start["end"] <= "17:00"


def test_a_booked_slot_disappears(ctx, provider, client):
    from calendar_logic import get_free_slots, is_slot_free
    from tests.conftest import register
    day = a_weekday()
    before = len(get_free_slots(provider["id"], day))

    token, _ = register(client, "taker@test.local")
    client.post("/api/appointments",
                json={"provider_id": provider["id"], "date": day,
                      "start_time": "10:00", "end_time": "10:15"},
                headers={"Authorization": f"Bearer {token}"})

    assert len(get_free_slots(provider["id"], day)) == before - 1
    assert not is_slot_free(provider["id"], day, "10:00", "10:15")


def test_a_booking_cannot_straddle_a_taken_slot(ctx, provider, client):
    """A 60-minute session must fail if any slot inside it is taken, not just
    the first one."""
    from calendar_logic import is_slot_free
    from tests.conftest import register
    day = a_weekday()
    token, _ = register(client, "straddle@test.local")
    client.post("/api/appointments",
                json={"provider_id": provider["id"], "date": day,
                      "start_time": "11:30", "end_time": "11:45"},
                headers={"Authorization": f"Bearer {token}"})
    assert not is_slot_free(provider["id"], day, "11:00", "12:00")
    assert is_slot_free(provider["id"], day, "11:45", "12:45")
