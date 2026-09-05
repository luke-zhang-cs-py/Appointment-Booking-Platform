"""The four emails that carry a coffee chat from ask to booking.

These go to somebody who may never have heard of Almanac and has no account,
which is why they are worded differently from everything else the platform
sends -- and why the text they contain is the text a host typed about
somebody, rather than anything the system generated.

Captured at mailer.send rather than read back out of email_log, because what
matters here is what was composed, not that a row exists.
"""

import datetime

import pytest

import coffee_chats as cc
import coffee_notifications as cn


@pytest.fixture
def outbox(monkeypatch):
    """Every Message that reaches the transport, in order."""
    import mailer
    box = []

    def capture(message):
        box.append(message)
        return True

    monkeypatch.setattr(mailer, "send", capture)
    return box


def an_invite(provider, **kw):
    return cc.create_invite(provider["id"], cc.InviteRequest(**kw))


# ------------------------------------------------------------------ invites

def test_the_invite_leads_with_who_is_asking(ctx, provider, outbox):
    invite = an_invite(provider, guest_email="ask@test.local", guest_name="Sam",
                       topic="a chat about graduate roles")
    cn.send_invite(invite["id"])

    (msg,) = outbox
    assert msg.kind == "coffee_invite" and msg.to == "ask@test.local"
    assert "Test Provider" in msg.subject
    assert "Hi Sam," in msg.text
    assert "a chat about graduate roles" in msg.text
    assert invite["token"] in msg.text, "the link works without a login"
    assert "sign in" not in msg.text.lower(), "no dashboard, no signup"


def test_an_invite_without_a_name_still_greets(ctx, provider, outbox):
    """The host may only have an address."""
    invite = an_invite(provider, guest_email="anon@test.local")
    cn.send_invite(invite["id"])
    assert "Hi," in outbox[0].text


def test_a_personal_note_is_carried_through(ctx, provider, outbox):
    invite = an_invite(provider, guest_email="note@test.local",
                       message="We met at the careers fair.")
    cn.send_invite(invite["id"])
    assert "We met at the careers fair." in outbox[0].text


def test_guest_text_reaches_the_html_escaped(ctx, provider, outbox):
    """A host types the guest's name. It ends up in an HTML email."""
    invite = an_invite(provider, guest_email="xss@test.local",
                       guest_name="<script>alert(1)</script>")
    cn.send_invite(invite["id"])
    assert "<script>" not in outbox[0].html
    assert "&lt;script&gt;" in outbox[0].html


def test_an_invite_that_is_not_there_sends_nothing(ctx, outbox):
    cn.send_invite(999999)
    assert outbox == []


def test_an_invite_whose_host_vanished_sends_nothing(ctx, provider, outbox, monkeypatch):
    """A missing host is not a reason to mail a stranger.

    The foreign key makes this hard to reach -- host_id cannot be repointed
    at a row that is not there -- so the guard is exercised directly. Code
    that cannot be reached from outside still runs when something upstream
    changes, and this one decides whether an email goes out.
    """
    invite = an_invite(provider, guest_email="orphan@test.local")
    monkeypatch.setattr(cn, "_host_of", lambda _invite: None)
    cn.send_invite(invite["id"])
    cn.send_nudge(invite["id"])
    cn.notify_declined(invite["id"])
    assert outbox == []


# ------------------------------------------------------------------- nudges

def test_a_nudge_to_somebody_who_never_opened_it(ctx, provider, outbox):
    invite = an_invite(provider, guest_email="quiet@test.local")
    cn.send_nudge(invite["id"])
    assert "busy moment" in outbox[0].text


def test_a_nudge_to_somebody_who_opened_it_says_something_else(ctx, provider, outbox):
    """Opened-but-not-booked is a different situation from never-opened, and
    the invite knows which, so the follow-up can at least not be wrong."""
    invite = cc.mark_viewed(an_invite(provider, guest_email="looked@test.local"))
    cn.send_nudge(invite["id"])
    assert "floating this back up" in outbox[0].text


def test_a_nudge_offers_an_out(ctx, provider, outbox):
    """Somebody who has not replied in three days is busy, not hostile."""
    invite = an_invite(provider, guest_email="polite@test.local")
    cn.send_nudge(invite["id"])
    assert "No reply needed" in outbox[0].text


def test_nudging_nothing_sends_nothing(ctx, outbox):
    cn.send_nudge(999999)
    assert outbox == []


# ------------------------------------------------------------------ booking

def test_the_host_hears_that_an_invite_converted(ctx, provider, offering, outbox):
    from calendar_logic import slot_starts_for
    invite = an_invite(provider, guest_email="converts@test.local",
                       guest_name="Ada", offering_id=offering["id"])
    day = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    start = slot_starts_for(provider["id"], day, invite["duration_min"])[0]["start"]
    booked, _ = cc.book(invite["token"], day, start, guest_name="Ada")

    cn.notify_booked(booked["id"])
    (msg,) = outbox
    assert msg.to == "provider@test.local"
    assert "Ada" in msg.subject
    assert offering["title"] in msg.text, "names the invite it came from"


def test_an_unbooked_invite_produces_no_booking_email(ctx, provider, outbox):
    invite = an_invite(provider, guest_email="notyet@test.local")
    cn.notify_booked(invite["id"])
    assert outbox == []


# ----------------------------------------------------------------- declines

def test_a_decline_reaches_the_host_with_the_reason(ctx, provider, outbox):
    invite = an_invite(provider, guest_email="busy@test.local", guest_name="Kit")
    cc.decline(invite["token"], "Swamped until the new year.")
    cn.notify_declined(invite["id"])

    (msg,) = outbox
    assert msg.to == "provider@test.local"
    assert "Kit" in msg.text
    assert "Swamped until the new year." in msg.text
    assert "nothing needs freeing up" in msg.text, "no slot was ever held"


def test_a_decline_without_a_reason_still_lands(ctx, provider, outbox):
    invite = an_invite(provider, guest_email="terse@test.local")
    cc.decline(invite["token"])
    cn.notify_declined(invite["id"])
    assert outbox[0].to == "provider@test.local"


def test_declining_nothing_sends_nothing(ctx, outbox):
    cn.notify_declined(999999)
    assert outbox == []


# -------------------------------------------------------------- the sweep

def test_the_sweep_nudges_the_quiet_and_closes_the_stale(ctx, provider, outbox):
    import database as db
    quiet = an_invite(provider, guest_email="sweep1@test.local")
    stale = an_invite(provider, guest_email="sweep2@test.local")

    old = (datetime.datetime.now()
           - datetime.timedelta(days=cc.NUDGE_AFTER_DAYS + 1)).strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("UPDATE coffee_invites SET created_at = ? WHERE id = ?", (old, quiet["id"]))
    db.execute("UPDATE coffee_invites SET expires_at = ? WHERE id = ?",
               ("2020-01-01T00:00:00", stale["id"]))

    assert cn.send_due_nudges() == {"nudged": 1, "expired": 1}
    assert [m.to for m in outbox] == ["sweep1@test.local"]
    assert cc.get_invite(quiet["id"])["nudge_count"] == 1
    assert cc.get_invite(stale["id"])["status"] == "expired"


def test_a_quiet_sweep_reports_nothing(ctx, provider, outbox):
    an_invite(provider, guest_email="fresh@test.local")
    assert cn.send_due_nudges() == {"nudged": 0, "expired": 0}
    assert outbox == []
