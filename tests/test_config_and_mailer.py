"""Startup safety, and the transport underneath every email.

Both are things that only misbehave somewhere you are not watching: a
placeholder signing key on a deployed box, and a console that cannot draw a
dash turning every development email into a delivery failure.
"""

import pytest

import config
import mailer


# ------------------------------------------------------------- SECRET_KEY

def test_the_committed_placeholder_is_reported():
    problem = config.secret_key_problem(config.DEV_SECRET_KEY)
    assert problem and "placeholder" in problem


def test_a_short_key_is_reported():
    """HS256 with a key shorter than the digest is not the algorithm it
    claims to be, and PyJWT says so on every encode."""
    problem = config.secret_key_problem("tiny")
    assert problem and "32" in problem


def test_a_real_key_passes():
    assert config.secret_key_problem("x" * config.MIN_SECRET_BYTES) is None


def test_debug_warns_and_carries_on():
    """Zero-setup local development is the point of every default in
    config.py, so the placeholder has to stay usable while DEBUG is on."""
    assert config.check_secret_key(config.DEV_SECRET_KEY, debug=True)


@pytest.mark.parametrize("key", ["dev-secret-change-me-in-production", "short", ""])
def test_production_refuses_to_start(key):
    """DEBUG off means this is a deployment. A deployment signing sessions
    with a published string should not come up at all -- failing at startup
    is loud, and a quietly forgeable admin token is not."""
    with pytest.raises(RuntimeError):
        config.check_secret_key(key, debug=False)


def test_production_starts_on_a_real_key():
    assert config.check_secret_key("k" * 48, debug=False) is None


def test_the_running_app_is_not_using_the_placeholder(app):
    """conftest sets SECRET_KEY, so this is really checking that the env var
    still wins over the default."""
    assert app.config["SECRET_KEY"] != config.DEV_SECRET_KEY


# ---------------------------------------------------------------- transport

def test_a_message_is_one_argument():
    """It used to be seven, in a fixed order, at eleven call sites."""
    msg = mailer.Message(kind="test", to="a@b.c", subject="s", text="t")
    assert msg.html == "" and msg.appointment_id is None and msg.log_id is None


def test_no_recipient_is_refused_without_raising(ctx):
    """Callers are side effects of something that already succeeded."""
    assert mailer.send(mailer.Message(kind="test", to="", subject="s", text="t")) is False


def test_mail_disabled_is_refused_without_raising(app):
    with app.app_context():
        app.config["MAIL_ENABLED"] = False
        try:
            assert mailer.send(
                mailer.Message(kind="test", to="a@b.c", subject="s", text="t")) is False
        finally:
            app.config["MAIL_ENABLED"] = True


def test_a_console_that_cannot_draw_a_dash_is_not_a_delivery_failure(ctx):
    """The details block puts an en dash between a start and an end time.
    Under cp437 or a bare C locale, printing one raises UnicodeEncodeError,
    _process catches it, and the email is recorded as failed -- in the mode
    where "sending" only ever meant printing.
    """
    import sys

    written = []

    class NarrowStdout:
        encoding = "ascii"

        def write(self, text):
            text.encode("ascii")           # what a narrow console does
            written.append(text)
            return len(text)

        def flush(self):
            pass

    original = sys.stdout
    sys.stdout = NarrowStdout()
    try:
        mailer._print("Time  09:00 – 09:15")
    finally:
        sys.stdout = original

    out = "".join(written)
    assert "09:00" in out and "09:15" in out, "the message still got through"
    assert "?" in out, "the one character it could not draw was replaced"


def test_the_failure_is_real_without_the_guard():
    """Pins the bug itself: the plain print this replaced does raise."""
    with pytest.raises(UnicodeEncodeError):
        "09:00 – 09:15".encode("cp437")


def test_a_failed_send_is_recorded_and_retryable(ctx, monkeypatch):
    """A mail server outage must not permanently swallow a reminder: the
    claim is handed back on the next attempt rather than counting as sent."""
    import database as db

    def explode(_message):
        raise OSError("smtp is down")

    monkeypatch.setattr(mailer, "_deliver", explode)
    assert mailer.send(mailer.Message(kind="test", to="fail@test.local",
                                      subject="s", text="t", appointment_id=None)) is True
    # Delivery is a background thread; the claim is synchronous but the
    # outcome is not.
    assert mailer.wait_until_idle(5.0), "delivery queue did not drain"
    row = db.query("SELECT * FROM email_log WHERE recipient = ?", ("fail@test.local",), one=True)
    assert row["status"] == "failed" and "smtp is down" in row["error"]

    monkeypatch.undo()
    assert mailer.send(mailer.Message(kind="test", to="fail@test.local",
                                      subject="s", text="t")) is True, "retried, not swallowed"
