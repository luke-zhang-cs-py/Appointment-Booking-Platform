"""
Turns a provider's recurring weekly availability + one-off blocks +
already-booked appointments into a list of free, bookable time slots
for a specific calendar date.
"""

import datetime as dt

import database as db


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _to_hhmm(total_minutes: int) -> str:
    return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def get_free_slots(provider_id: int, date_str: str):
    """
    date_str: "YYYY-MM-DD"
    Returns a sorted list of {"start": "HH:MM", "end": "HH:MM"} slots.
    """
    target_date = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    weekday = target_date.weekday()  # Monday=0 ... Sunday=6, matches ISO
    # We store Sunday=0..Saturday=6 (common calendar-UI convention), convert:
    day_of_week = (weekday + 1) % 7

    windows = db.query(
        "SELECT start_time, end_time, slot_minutes FROM availability "
        "WHERE provider_id = ? AND day_of_week = ?",
        (provider_id, day_of_week),
    )
    if not windows:
        return []

    # Whole-day blocks (no start/end) wipe out all windows for the date.
    day_blocks = db.query(
        "SELECT start_time, end_time FROM blocked_slots "
        "WHERE provider_id = ? AND date = ?",
        (provider_id, date_str),
    )
    if any(b["start_time"] is None for b in day_blocks):
        return []

    partial_blocks = [
        (_to_minutes(b["start_time"]), _to_minutes(b["end_time"]))
        for b in day_blocks
        if b["start_time"] is not None
    ]

    booked = db.query(
        "SELECT start_time, end_time FROM appointments "
        "WHERE provider_id = ? AND date = ? AND status = 'confirmed'",
        (provider_id, date_str),
    )
    booked_ranges = [(_to_minutes(a["start_time"]), _to_minutes(a["end_time"])) for a in booked]

    now = dt.datetime.now()
    is_today = target_date == now.date()
    now_minutes = now.hour * 60 + now.minute

    slots = []
    for w in windows:
        start = _to_minutes(w["start_time"])
        end = _to_minutes(w["end_time"])
        step = w["slot_minutes"]
        cursor = start
        while cursor + step <= end:
            slot_start, slot_end = cursor, cursor + step
            cursor += step

            if is_today and slot_start <= now_minutes:
                continue  # don't offer slots already in the past today

            overlaps = any(
                slot_start < b_end and slot_end > b_start
                for b_start, b_end in booked_ranges + partial_blocks
            )
            if overlaps:
                continue

            slots.append({"start": _to_hhmm(slot_start), "end": _to_hhmm(slot_end)})

    slots.sort(key=lambda s: s["start"])
    return slots


def is_slot_free(provider_id: int, date_str: str, start_time: str, end_time: str) -> bool:
    free = get_free_slots(provider_id, date_str)
    return any(s["start"] == start_time and s["end"] == end_time for s in free)
