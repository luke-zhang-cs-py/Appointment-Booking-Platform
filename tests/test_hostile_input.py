"""Rows that should never have been storable, and rows already stored.

Everything in this file is a bug that was reachable from the API by a
logged-in provider changing their own hours. Two of them took the whole
request down -- one by raising inside the slot engine, one by never
returning at all -- and both were then hit by every *client* trying to book,
and by every guest opening a coffee chat link.

So there are two halves to each: the route refuses to create it, and the
engine survives one that is already in the table. Validation added today
does nothing for a database that has been running for a month.
"""

import datetime
import threading

import pytest
from flask import current_app

from calendar_logic import _tile, get_free_slots


def a_date():
    return (datetime.date.today() + datetime.timedelta(days=4)).isoformat()


def day_of_week(date_str):
    """The project stores Sunday=0; Python has Monday=0."""
    return (datetime.date.fromisoformat(date_str).weekday() + 1) % 7


def within(seconds, fn, *args):
    """Run fn, failing if it does not finish -- rather than hanging pytest.

    `slot_minutes = 0` made get_free_slots loop forever appending to a list.
    A test for that cannot simply call it.
    """
    box = {}
    # The work happens on another thread, which has no Flask application
    # context of its own and therefore no database connection.
    app = current_app._get_current_object()

    def run():
        try:
            with app.app_context():
                box["value"] = fn(*args)
        except BaseException as exc:      # noqa: BLE001 - reported below
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=seconds)
    if thread.is_alive():
        pytest.fail(f"{fn.__name__} did not return within {seconds}s -- it is looping")
    if "error" in box:
        raise box["error"]
    return box["value"]


def store_window(provider_id, date_str, **over):
    """Make this the provider's only window, bypassing the route.

    This is what a database that predates the validation looks like. The
    provider fixture comes with a full week already, so that is cleared
    first -- otherwise a test for "the bad row is skipped" passes on the
    strength of the good rows next to it.
    """
    import database as db
    db.execute("DELETE FROM availability WHERE provider_id = ?", (provider_id,))
    row = {"day_of_week": day_of_week(date_str), "start_time": "09:00",
           "end_time": "17:00", "slot_minutes": 30}
    row.update(over)
    db.execute(
        "INSERT INTO availability (provider_id, day_of_week, start_time, end_time, "
        "slot_minutes) VALUES (?, ?, ?, ?, ?)",
        (provider_id, row["day_of_week"], row["start_time"], row["end_time"],
         row["slot_minutes"]))


def store_block(provider_id, date_str, start_time=None, end_time=None):
    import database as db
    db.execute(
        "INSERT INTO blocked_slots (provider_id, date, start_time, end_time) "
        "VALUES (?, ?, ?, ?)", (provider_id, date_str, start_time, end_time))


# ------------------------------------------------- the window that hung it

@pytest.mark.parametrize("slot_minutes", [0, -5, -1, 24 * 60 + 1])
def test_an_unusable_slot_size_is_refused(client, provider, slot_minutes):
    """Zero is the one that mattered. The engine walks a window in
    slot_minutes steps, so a zero step never advances: it appended slots
    until memory ran out and took the worker thread with it."""
    res = client.post("/api/availability/mine", headers=provider["auth"], json={
        "day_of_week": 1, "start_time": "09:00", "end_time": "17:00",
        "slot_minutes": slot_minutes})
    assert res.status_code == 400
    assert "slot_minutes" in res.get_json()["error"]


def test_a_stored_zero_slot_size_does_not_hang_the_engine(ctx, provider):
    """The half that validation cannot fix: a row written before today."""
    day = a_date()
    store_window(provider["id"], day, slot_minutes=0)
    assert within(5, get_free_slots, provider["id"], day) == []


def test_a_stored_negative_slot_size_does_not_hang_either(ctx, provider):
    day = a_date()
    store_window(provider["id"], day, slot_minutes=-30)
    assert within(5, get_free_slots, provider["id"], day) == []


def test_tile_refuses_a_non_advancing_step_directly(ctx):
    """Pinned at the loop itself, so a future rewrite of _tile has to keep
    the guard."""
    for step in (0, -15, None):
        window = {"start_time": "09:00", "end_time": "17:00", "slot_minutes": step}
        assert within(5, _tile, window, [], -1) == []


def test_a_usable_window_still_tiles(ctx, provider):
    """The guard must not have made every window unusable."""
    day = a_date()
    store_window(provider["id"], day, slot_minutes=60)
    slots = get_free_slots(provider["id"], day)
    assert len(slots) == 8 and slots[0] == {"start": "09:00", "end": "10:00"}


# ------------------------------------------------- times that are not times

@pytest.mark.parametrize("start,end", [
    ("banana", "cherry"), ("25:00", "26:00"), ("9:00", "17:00"),
    ("09:60", "17:00"), ("", ""), (None, None),
])
def test_a_window_whose_times_are_not_times_is_refused(client, provider, start, end):
    """These reached the engine as a ValueError out of str.split(':') and
    came back as a 500 from the booking endpoint -- stored once by the
    provider, hit by every client afterwards."""
    res = client.post("/api/availability/mine", headers=provider["auth"], json={
        "day_of_week": 1, "start_time": start, "end_time": end})
    assert res.status_code == 400


def test_a_stored_unreadable_window_is_skipped_not_raised(ctx, provider):
    day = a_date()
    store_window(provider["id"], day, start_time="banana", end_time="cherry")
    assert get_free_slots(provider["id"], day) == []


def test_a_window_must_start_before_it_ends(client, provider):
    res = client.post("/api/availability/mine", headers=provider["auth"], json={
        "day_of_week": 1, "start_time": "17:00", "end_time": "09:00"})
    assert res.status_code == 400


# ------------------------------------------------------- half-written blocks

def test_a_block_with_a_start_and_no_end_is_refused(client, provider):
    """Accepted before, and then _to_minutes(None) raised AttributeError on
    every slot lookup and every booking for that provider on that date."""
    res = client.post("/api/availability/mine/block", headers=provider["auth"],
                      json={"date": a_date(), "start_time": "10:00"})
    assert res.status_code == 400
    assert "both" in res.get_json()["error"]


def test_a_block_with_an_end_and_no_start_is_refused(client, provider):
    res = client.post("/api/availability/mine/block", headers=provider["auth"],
                      json={"date": a_date(), "end_time": "10:00"})
    assert res.status_code == 400


@pytest.mark.parametrize("date_str", ["nonsense", "05-09-2026", "2026-9-8", ""])
def test_a_block_needs_a_real_date(client, provider, date_str):
    res = client.post("/api/availability/mine/block", headers=provider["auth"],
                      json={"date": date_str})
    assert res.status_code == 400


def test_a_block_must_start_before_it_ends(client, provider):
    """Accepted before, and then blocked nothing at all: the overlap test can
    never be true for a backwards range, so it was silently inert."""
    res = client.post("/api/availability/mine/block", headers=provider["auth"],
                      json={"date": a_date(), "start_time": "15:00", "end_time": "10:00"})
    assert res.status_code == 400


def test_a_stored_half_block_blocks_rather_than_raising(ctx, provider):
    """Fail-safe direction: "blocked from 14:00" is the natural reading of a
    row with a start and no end, and it is the one that cannot double-book."""
    day = a_date()
    store_window(provider["id"], day)
    store_block(provider["id"], day, start_time="14:00")
    slots = within(5, get_free_slots, provider["id"], day)
    assert slots, "the morning survives"
    assert all(s["end"] <= "14:00" for s in slots), "nothing after the block"


def test_a_stored_unreadable_block_takes_the_whole_day(ctx, provider):
    """A block nobody can interpret blocks the day rather than quietly
    disappearing. Losing an hour is an inconvenience; double-booking
    somebody is the thing the slot engine exists to prevent."""
    day = a_date()
    store_window(provider["id"], day)
    store_block(provider["id"], day, start_time="banana", end_time="cherry")
    assert within(5, get_free_slots, provider["id"], day) == []


def test_a_whole_day_block_still_works(ctx, provider):
    day = a_date()
    store_window(provider["id"], day)
    store_block(provider["id"], day)
    assert get_free_slots(provider["id"], day) == []


def test_booking_survives_a_poisoned_calendar(client, provider, booking):
    """The reason any of this matters: the bad row was the provider's, and
    the 500 was the client's."""
    import database as db
    day = booking["date"]
    db.execute("INSERT INTO blocked_slots (provider_id, date, start_time, end_time) "
               "VALUES (?, ?, ?, ?)", (provider["id"], day, "banana", None))
    res = client.post("/api/appointments", headers=booking["auth"], json={
        "provider_id": provider["id"], "date": day,
        "start_time": "14:00", "end_time": "14:30"})
    assert res.status_code != 500
