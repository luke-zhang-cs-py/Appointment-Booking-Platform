"""The one background timer.

A dead scheduler is silent. Nothing raises, no request fails, and the first
symptom is somebody missing an appointment a day later -- so the behaviour
worth pinning is that neither half of the sweep can take the other down with
it.
"""

import scheduler


def test_a_sweep_runs_both_halves(ctx):
    result = scheduler.sweep()
    assert set(result) == {"reminders", "coffee"}
    assert result["reminders"] == 0
    assert result["coffee"] == {"nudged": 0, "expired": 0}


def test_a_failing_nudge_sweep_does_not_stop_reminders(ctx, monkeypatch, booking):
    """These ride one tick precisely so there is one thing to go wrong. That
    only helps if one going wrong does not take the other with it."""
    import coffee_notifications

    def explode(now=None):
        raise RuntimeError("coffee is off")

    monkeypatch.setattr(coffee_notifications, "send_due_nudges", explode)
    result = scheduler.sweep()
    assert result["coffee"] is None, "recorded as failed"
    assert result["reminders"] == 0, "and the other half still ran"


def test_a_failing_reminder_scan_does_not_stop_nudges(ctx, monkeypatch):
    import notifications

    def explode(now=None):
        raise RuntimeError("database went away")

    monkeypatch.setattr(notifications, "send_due_reminders", explode)
    result = scheduler.sweep()
    assert result["reminders"] is None
    assert result["coffee"] == {"nudged": 0, "expired": 0}


def test_disabled_reminders_start_nothing(app):
    original = app.config["REMINDERS_ENABLED"]
    app.config["REMINDERS_ENABLED"] = False
    try:
        scheduler.start(app)
        assert scheduler._thread is None or not scheduler._thread.is_alive()
    finally:
        app.config["REMINDERS_ENABLED"] = original


def test_the_reloader_child_owns_the_timer(app, monkeypatch):
    """Flask's reloader runs the app twice. Two schedulers would double every
    scan -- harmless because of the de-duplication index, and still twice the
    work and twice the log noise."""
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    app.debug = True
    scheduler.start(app)
    assert scheduler._thread is None or not scheduler._thread.is_alive()


def test_the_scan_interval_has_a_floor(app):
    """A misconfigured 0 would spin the thread against the database."""
    assert scheduler.MIN_INTERVAL_SECONDS >= 60


def test_the_loop_exits_when_told_to(app):
    """_stop is checked before the first sleep and after every wake, so a
    shutdown does not have to wait out a fifteen-minute interval."""
    scheduler.stop()
    try:
        scheduler._loop(app, interval=0.01)      # returns rather than looping
    finally:
        scheduler._stop.clear()


def test_a_non_debug_process_does_start_a_thread(app):
    was_debug = app.debug
    app.debug = False
    try:
        scheduler.start(app)
        assert scheduler._thread is not None and scheduler._thread.is_alive()
        scheduler.stop()
        scheduler._thread.join(timeout=5)
        assert not scheduler._thread.is_alive(), "and stops when asked"
    finally:
        app.debug = was_debug
        scheduler._stop.clear()


def test_starting_twice_does_not_stack_timers(app):
    was_debug = app.debug
    app.debug = False
    try:
        scheduler.start(app)
        first = scheduler._thread
        scheduler.start(app)
        assert scheduler._thread is first
    finally:
        scheduler.stop()
        if scheduler._thread:
            scheduler._thread.join(timeout=5)
        app.debug = was_debug
        scheduler._stop.clear()
