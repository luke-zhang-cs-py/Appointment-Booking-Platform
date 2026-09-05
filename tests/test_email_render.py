"""The layout layer, which two notification modules now share.

Worth its own file for one reason: this is where guest-supplied text becomes
HTML. An invite carries a name and a message written by whoever the host is
inviting, and the escaping below is the only thing between that and the
recipient's mail client.
"""

import datetime as dt

import pytest

from email_render import (details, escape, lead_time, long_date, render, url,
                          when)


def an_appointment(**over):
    base = {
        "date": "2026-09-08", "start_time": "14:30", "end_time": "15:00",
        "status": "confirmed", "notes": None, "provider_specialty": None,
        "provider_name": "Dr Who", "client_name": "Ada",
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ layout

def test_both_halves_carry_the_same_facts(ctx):
    """The plain-text half is not a courtesy copy -- it is what a screen
    reader and a text-only client actually get."""
    text, html = render(
        title="Your appointment is confirmed",
        intro=["Hi Ada, you're booked in."],
        details=[("Provider", "Dr Who"), ("Date", "Tuesday, 8 September 2026")],
        action=("View my appointments", "http://localhost:5000/"),
        outro="Cancel from your dashboard.",
    )
    for fragment in ("Dr Who", "Tuesday, 8 September 2026",
                     "http://localhost:5000/", "Cancel from your dashboard."):
        assert fragment in text, fragment
        assert fragment in html, fragment
    assert "Hi Ada" in text and "Hi Ada" in html


def test_empty_intro_lines_are_dropped(ctx):
    """send_welcome passes a role line that is empty for unknown roles, and a
    blank paragraph in an email looks like a bug."""
    text, html = render(title="T", intro=["Only line", "", None], details=[("A", "b")])
    assert text.count("Only line") == 1
    assert "<p" in html and html.count("margin:0 0 12px") == 1


def test_action_and_outro_are_optional(ctx):
    text, html = render(title="T", intro=["x"], details=[("A", "b")])
    assert "href" not in html
    assert text.rstrip().endswith("You're receiving this because you have an Almanac account.")


def test_details_are_column_aligned_in_text(ctx):
    text, _ = render(title="T", intro=["x"],
                     details=[("A", "1"), ("Much longer label", "2")])
    lines = [ln for ln in text.split("\n") if ln.startswith("  ")]
    assert lines[0].index("1") == lines[1].index("2"), "labels padded to one width"


def test_no_details_does_not_crash(ctx):
    """max() over an empty sequence is the obvious way to write that padding
    and the obvious way to raise ValueError."""
    text, _ = render(title="T", intro=["x"], details=[])
    assert "T" in text


# ---------------------------------------------------------------- escaping

@pytest.mark.parametrize("raw,banned", [
    ("<script>alert(1)</script>", "<script>"),
    ('" onmouseover="alert(1)', '" onmouseover'),
    ("Ben & Jerry <b>", "<b>"),
])
def test_guest_text_cannot_inject_html(ctx, raw, banned):
    """A guest name comes from whoever the host typed it for, and reaches the
    HTML half of the invite email."""
    _, html = render(title="Invite", intro=[f"Hi {raw},"], details=[("Guest", raw)])
    assert banned not in html
    assert "&lt;" in html or "&quot;" in html or "&amp;" in html


def test_escape_leaves_single_quotes_alone(ctx):
    """Deliberate: every attribute this module writes is double-quoted, and
    turning O'Brien into O&#39;Brien in the plain-text-adjacent parts is
    noise. If an attribute ever becomes single-quoted, this stops being safe."""
    assert escape("O'Brien") == "O'Brien"
    assert escape('say "hi"') == "say &quot;hi&quot;"


def test_escape_handles_non_strings(ctx):
    assert escape(30) == "30"
    assert escape(None) == "None"


# ------------------------------------------------------------------- dates

def test_long_date_has_no_leading_zero_and_no_glibc_format(ctx):
    """strftime('%-d') is a glibc extension that raises ValueError on
    Windows. This project deploys to both."""
    assert long_date("2026-09-08") == "Tuesday, 8 September 2026"


@pytest.mark.parametrize("bad", ["not a date", "", None, "2026-13-45"])
def test_long_date_passes_through_what_it_cannot_parse(ctx, bad):
    assert long_date(bad) == bad


def test_when_is_short_enough_for_a_subject_line(ctx):
    assert when(an_appointment()) == "Tue 8 Sep, 14:30"


def test_when_degrades_instead_of_raising(ctx):
    """A subject line is not worth a 500."""
    assert when({"date": "garbage", "start_time": "14:30"}) == "garbage 14:30"
    assert when({}) == ""


@pytest.mark.parametrize("start,expected", [
    ("2026-09-08 12:30", "in about 2 hours (today at 14:30)"),
    ("2026-09-08 06:00", "today at 14:30"),
    ("2026-09-07 09:00", "tomorrow at 14:30"),
    ("2026-09-01 09:00", "in 7 days, on Tuesday, 8 September 2026 at 14:30"),
])
def test_lead_time_words_the_gap(ctx, start, expected):
    now = dt.datetime.strptime(start, "%Y-%m-%d %H:%M")
    assert lead_time(an_appointment(), now) == expected


def test_lead_time_singular_hour(ctx):
    now = dt.datetime(2026, 9, 8, 13, 45)
    assert "1 hour (" in lead_time(an_appointment(), now)


def test_lead_time_survives_a_malformed_appointment(ctx):
    appt = an_appointment(date="nonsense")
    assert lead_time(appt, dt.datetime.now()).startswith("coming up on nonsense")


# ----------------------------------------------------------------- details

def test_service_row_only_when_there_is_a_specialty(ctx):
    with_spec = details(an_appointment(provider_specialty="Careers"),
                        counterpart=("Provider", "Dr Who"))
    assert ("Service", "Careers") in with_spec
    without = details(an_appointment(), counterpart=("Provider", "Dr Who"))
    assert not any(label == "Service" for label, _ in without)


def test_specialty_is_not_shown_to_the_provider(ctx):
    """It is their own specialty. The client is the one who needs telling."""
    rows = details(an_appointment(provider_specialty="Careers"),
                   counterpart=("Client", "Ada"))
    assert not any(label == "Service" for label, _ in rows)


def test_notes_appear_only_when_written(ctx):
    assert ("Notes", "Bring a laptop") in details(
        an_appointment(notes="Bring a laptop"), counterpart=("Client", "Ada"))
    assert not any(label == "Notes"
                   for label, _ in details(an_appointment(), counterpart=("Client", "Ada")))


def test_status_can_be_overridden(ctx):
    """The row is updated after the email is composed, so a cancellation
    notice would otherwise say 'confirmed'."""
    rows = details(an_appointment(), counterpart=("Client", "Ada"), status="cancelled")
    assert ("Status", "cancelled") in rows


def test_url_is_absolute(ctx):
    """Relative links are useless in a mail client."""
    assert url("/coffee/abc").startswith("http://")
