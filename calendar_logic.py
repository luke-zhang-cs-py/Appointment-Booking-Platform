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


MINUTES_IN_A_DAY = 24 * 60


def get_free_slots(provider_id: int, date_str: str):
    """
    date_str: "YYYY-MM-DD"
    Returns a sorted list of {"start": "HH:MM", "end": "HH:MM"} slots.
    """
    target_date = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    windows = _windows_for(provider_id, target_date)
    if not windows:
        return []

    busy = _busy_ranges(provider_id, date_str)
    now = dt.datetime.now()
    # Only today has a past to exclude. -1 is before every slot start, so
    # every other date compares against something no slot can be at or below.
    earliest = (now.hour * 60 + now.minute) if target_date == now.date() else -1

    slots = [slot for w in windows for slot in _tile(w, busy, earliest)]
    slots.sort(key=lambda s: s["start"])
    return slots


def _windows_for(provider_id, target_date):
    """The provider's recurring hours for this weekday."""
    # Python has Monday=0; we store Sunday=0, the common calendar-UI
    # convention. Convert rather than storing two of anything.
    day_of_week = (target_date.weekday() + 1) % 7
    return db.query(
        "SELECT start_time, end_time, slot_minutes FROM availability "
        "WHERE provider_id = ? AND day_of_week = ?",
        (provider_id, day_of_week),
    )


def _busy_ranges(provider_id, date_str):
    """Minute ranges on this date that cannot be booked over.

    One-off blocks and confirmed appointments are the same thing here: time
    that is already spoken for. A block with no start and end means the whole
    day, which is expressed as a range covering it rather than as a separate
    kind of answer -- a caller that has to check for a special value is a
    caller that can forget to.
    """
    blocks = db.query(
        "SELECT start_time, end_time FROM blocked_slots "
        "WHERE provider_id = ? AND date = ?",
        (provider_id, date_str),
    )
    if any(b["start_time"] is None for b in blocks):
        return [(0, MINUTES_IN_A_DAY)]

    ranges = [(_to_minutes(b["start_time"]), _to_minutes(b["end_time"])) for b in blocks]

    booked = db.query(
        "SELECT start_time, end_time FROM appointments "
        "WHERE provider_id = ? AND date = ? AND status = 'confirmed'",
        (provider_id, date_str),
    )
    return ranges + [(_to_minutes(a["start_time"]), _to_minutes(a["end_time"]))
                     for a in booked]


def _tile(window, busy, earliest):
    """One availability window cut into free slot-sized pieces."""
    end = _to_minutes(window["end_time"])
    step = window["slot_minutes"]

    out = []
    cursor = _to_minutes(window["start_time"])
    while cursor + step <= end:
        slot_start, slot_end = cursor, cursor + step
        cursor += step
        if slot_start <= earliest:
            continue
        if any(slot_start < busy_end and slot_end > busy_start
               for busy_start, busy_end in busy):
            continue
        out.append({"start": _to_hhmm(slot_start), "end": _to_hhmm(slot_end)})
    return out


def is_slot_free(provider_id: int, date_str: str, start_time: str, end_time: str) -> bool:
    """Is [start_time, end_time) bookable, across however many slots it spans?

    This used to require an exact match against one generated slot:

        any(s["start"] == start_time and s["end"] == end_time for s in free)

    which quietly made any appointment longer than the availability grid
    impossible. A provider offering 30-minute slots could never take a
    60-minute booking, because no generated slot is 60 minutes long -- and
    the caller reported it as "someone just took that slot" when nothing was
    booked at all, which is the worst kind of wrong error.

    Walking consecutive free slots instead means a booking is free when every
    slot it covers is free and it ends exactly on a boundary. A single-slot
    booking still behaves identically, so nothing that worked before changes.
    """
    free = get_free_slots(provider_id, date_str)
    if not free:
        return False

    next_boundary = {s["start"]: s["end"] for s in free}
    cursor = start_time
    while cursor < end_time:
        following = next_boundary.get(cursor)
        if following is None:
            return False          # not the start of a free slot
        if following > end_time:
            return False          # the last slot would overrun the booking
        cursor = following
    return cursor == end_time


def slot_starts_for(provider_id: int, date_str: str, duration_min: int):
    """Start times on this date that can actually hold `duration_min`.

    The booking page needs this rather than the raw slot list: offering a
    guest a 16:30 start for a 60-minute session when the day ends at 17:00 is
    an invitation to hit an error.
    """
    free = get_free_slots(provider_id, date_str)
    out = []
    for slot in free:
        start = slot["start"]
        total = _to_minutes(start) + duration_min
        end = _to_hhmm(total)
        if total <= 24 * 60 and is_slot_free(provider_id, date_str, start, end):
            out.append({"start": start, "end": end})
    return out
