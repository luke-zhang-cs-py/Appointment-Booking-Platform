"""One timestamp format, one timezone, every table, both backends.

These columns are TEXT and get compared as strings, so a difference in
spelling is a difference in meaning. They were spelled three ways:

    SQLite DEFAULT datetime('now')        "2026-09-05 22:30:15"  UTC
    written by the application            "2026-09-05T18:30:15"  local
    Postgres DEFAULT NOW()                a timestamp, not text

A space sorts before a "T", so a created_at on the same calendar date as a
cutoff always sorted before it, whatever the actual times were -- which is
how an invite could be chased a day early. The UTC/local gap moved the
boundary again by the machine's offset.
"""

import datetime as dt

import pytest

import database as db

TIMESTAMPED = [
    ("users", "created_at"),
    ("appointments", "created_at"),
    ("email_log", "created_at"),
    ("coffee_invites", "created_at"),
    ("offerings", "created_at"),
]


def parses(value):
    return dt.datetime.strptime(value, db.TIMESTAMP_FORMAT)


# ------------------------------------------------------------------ format

def test_every_default_matches_the_one_format(ctx, provider, booking, offering):
    """Whatever wrote the row -- a schema default or the application."""
    import coffee_chats as cc
    cc.create_invite(provider["id"], cc.InviteRequest("stamp@test.local"))

    for table, column in TIMESTAMPED:
        rows = db.query(f"SELECT {column} AS value FROM {table}")
        assert rows, f"{table} had no rows to check"
        for row in rows:
            parses(row["value"])          # raises if the spelling drifted


def test_the_default_is_local_time_not_utc(ctx, provider):
    """Every other time in this project is local -- a provider's 09:00 means
    09:00 where they are. A created_at in UTC was the odd one out, and it was
    being compared against the others."""
    import coffee_chats as cc
    invite = cc.create_invite(provider["id"], cc.InviteRequest("tz@test.local"))
    written = parses(invite["created_at"])
    drift = abs((dt.datetime.now() - written).total_seconds())
    assert drift < 120, f"created_at is {drift / 3600:.1f}h from local now"


def test_the_schema_default_and_the_application_agree(ctx, provider):
    """created_at comes from the database, expires_at from the application,
    and due_for_nudge compares one against the other."""
    import coffee_chats as cc
    invite = cc.create_invite(provider["id"], cc.InviteRequest("agree@test.local"))
    created, expires = invite["created_at"], invite["expires_at"]
    assert len(created) == len(expires)
    assert created[10] == expires[10] == "T", "same separator"
    assert created < expires, "and therefore comparable as strings"


@pytest.mark.parametrize("table,column", TIMESTAMPED)
def test_the_format_sorts_chronologically(ctx, table, column):
    """The reason this format is worth standardising on: string order is
    time order, so ORDER BY created_at needs no parsing."""
    early = dt.datetime(2026, 1, 2, 3, 4, 5).strftime(db.TIMESTAMP_FORMAT)
    late = dt.datetime(2026, 1, 2, 3, 4, 6).strftime(db.TIMESTAMP_FORMAT)
    assert early < late
    assert dt.datetime(2026, 1, 2).strftime(db.TIMESTAMP_FORMAT) < early


# ----------------------------------------------------- what it actually broke

def test_an_invite_is_not_nudged_before_the_interval_is_up(ctx, provider):
    """The bug, precisely.

    created_at is left as the database wrote it, and `now` is chosen so the
    three-day cutoff lands on the same calendar date as it, slightly earlier
    in the day. Just under three days have passed, so nothing is due.

    With the old spelling the comparison was "2026-09-05 22:00:00" against
    "2026-09-05T21:30:00", the space sorted first, and this invite got chased
    early.
    """
    import coffee_chats as cc
    invite = cc.create_invite(provider["id"], cc.InviteRequest("early@test.local"))
    created = parses(invite["created_at"])

    # An earlier moment on the same calendar date as created_at.
    cutoff = created.replace(minute=0, second=0) - dt.timedelta(hours=1)
    if cutoff.date() != created.date():          # created just after midnight
        cutoff = created.replace(hour=0, minute=0, second=0)
    now = cutoff + dt.timedelta(days=cc.NUDGE_AFTER_DAYS)

    assert (now - created) < dt.timedelta(days=cc.NUDGE_AFTER_DAYS), "not yet due"
    assert cc.due_for_nudge(now=now) == [], "chased early"


def test_an_invite_is_nudged_once_the_interval_really_is_up(ctx, provider):
    """The other half: the fix must not have stopped nudges altogether."""
    import coffee_chats as cc
    invite = cc.create_invite(provider["id"], cc.InviteRequest("due@test.local"))
    created = parses(invite["created_at"])
    now = created + dt.timedelta(days=cc.NUDGE_AFTER_DAYS, hours=1)
    assert [i["id"] for i in cc.due_for_nudge(now=now)] == [invite["id"]]


def test_a_message_is_never_sent_before_it_was_queued(ctx, provider):
    """created_at came from the schema in UTC and sent_at from the
    application in local time, so on any machine west of Greenwich the
    delivery log showed messages sent hours before they were queued."""
    import mailer
    mailer.send(mailer.Message(kind="test", to="order@test.local",
                               subject="s", text="t"))
    assert mailer.wait_until_idle(5.0)
    row = db.query("SELECT created_at, sent_at FROM email_log WHERE recipient = ?",
                   ("order@test.local",), one=True)
    assert row["sent_at"] >= row["created_at"]
    parses(row["sent_at"])


def test_now_stamp_is_the_format_everything_else_uses(ctx):
    parses(db.now_stamp())
    assert db.now_stamp()[10] == "T"
