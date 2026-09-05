"""
scheduler.py
-------------
The one background timer.

Split out of notifications.py, which was deciding what to say, how it should
look, and when to wake up. Timing is its own concern and its own failure
mode: everything else in this project runs inside a request and fails where
somebody can see it, while this runs on a thread nobody is watching.

Deliberately a plain thread rather than Celery or APScheduler. It keeps the
project dependency-free and one process is plenty at this scale. If you ever
run several web workers they will all scan, and the unique index on email_log
means people still get one message each. On a bigger deployment you would
drop this and point cron at POST /api/admin/emails/run-reminders and
POST /api/coffee/run-nudges instead, which is why both of those endpoints
exist.
"""

import logging
import os
import threading

import coffee_notifications
import notifications

log = logging.getLogger("almanac.scheduler")

# The minimum gap between scans, whatever REMINDER_SCAN_MINUTES says. A
# misconfigured 0 would otherwise spin the thread against the database.
MIN_INTERVAL_SECONDS = 60.0

# How long to let the app finish booting before the first scan.
STARTUP_DELAY_SECONDS = 20.0

_thread = None
_lock = threading.Lock()
_stop = threading.Event()


def start(app):
    """Run the sweep every REMINDER_SCAN_MINUTES in a daemon thread."""
    global _thread

    if not app.config["REMINDERS_ENABLED"]:
        log.info("reminders disabled (REMINDERS_ENABLED=0)")
        return
    # Flask's reloader runs the app twice; let the child process own the timer.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        interval = max(MIN_INTERVAL_SECONDS, app.config["REMINDER_SCAN_MINUTES"] * 60)
        _stop.clear()
        _thread = threading.Thread(target=_loop, args=(app, interval),
                                   name="almanac-reminders", daemon=True)
        _thread.start()
        log.info("scheduler started: every %.0f min, %.0f h ahead",
                 interval / 60, app.config["REMINDER_HOURS_BEFORE"])


def stop():
    _stop.set()


def _loop(app, interval):
    _stop.wait(STARTUP_DELAY_SECONDS)
    while not _stop.is_set():
        with app.app_context():
            sweep()
        _stop.wait(interval)


def sweep():
    """One pass: appointment reminders, then coffee chat follow-ups.

    Both rides on one tick rather than two timers, so the process keeps one
    background loop and one place for it to go wrong. Each half is isolated:
    a failing nudge sweep must not stop reminders going out, and neither may
    kill the thread, because a dead scheduler is silent and nobody notices
    until the day somebody misses an appointment.
    """
    results = {}
    for name, run in (("reminders", notifications.send_due_reminders),
                      ("coffee", coffee_notifications.send_due_nudges)):
        try:
            results[name] = run()
        except Exception:
            log.exception("%s sweep failed", name)
            results[name] = None
    return results
