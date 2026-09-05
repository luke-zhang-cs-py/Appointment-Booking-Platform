# Code audit

Static analysis (flake8, radon), an 82-test suite, and a coverage report.

```bash
python -m pytest tests/ --cov=. --cov-report=term-missing
python -m flake8 . --select=E9,F63,F7,F82,F401,F811,F841,E722 --exclude=.git
python -m radon cc . -s -n C --exclude ".git/*"
```

## Coverage

82 tests, **73%** overall, from 0% before the audit.

| Module | Cover | | Module | Cover |
|---|---|---|---|---|
| `offerings.py` | **100%** | | `database.py` | 79% |
| `config.py` | **100%** | | `mailer.py` | 70% |
| `calendar_logic.py` | 95% | | `coffee_notifications.py` | 68% |
| `app.py` | 91% | | `routes/auth_routes.py` | 68% |
| `coffee_chats.py` | 89% | | `routes/availability_routes.py` | 53% |
| `routes/coffee_routes.py` | 89% | | `routes/user_routes.py` | 48% |
| `auth.py` | 88% | | `notifications.py` | 48% |
| `routes/offering_routes.py` | 85% | | `routes/appointment_routes.py` | 46% |

The weakest are the email bodies and the older CRUD routes -- both written
before the audit and neither exercised by the new tests, which concentrated
on the booking maths and the two features added most recently.

## The bug worth reporting

**`login_page.py` could not log anybody in.** It builds a JWT with an integer
`sub`:

```python
"sub": user["id"],          # login_page.py
"sub": str(user["id"]),     # auth.py
```

`auth.py` carries a comment explaining that RFC 7519 requires a string and
PyJWT >= 2.10 enforces it. That fix reached one copy and not the other.
Confirmed against the installed PyJWT 2.13: encoding succeeds, and every
decode raises `InvalidSubjectError: Subject must be a string`, so the
standalone server issued tokens that it then rejected on the next request.
*Fixed*, and it now round-trips.

This is what duplicated code costs. The file is deliberately self-contained
-- it is documented as runnable on its own with its own database, so unlike
a stale copy it has a reason to exist -- but it re-implements `create_token`,
`register`, `login`, `me`, `get_db` and `init_db`, and a security-relevant
fix in the real module silently did not apply to it. Left standalone, since
that is its purpose, with the divergence risk noted here.

## Findings

### Dispensables

- **Dead code:** `owns_conn` assigned and never read, twice in `database.py`.
  An unused import in `coffee_chats.py`. *Fixed.*
- **Duplicate code:** `login_page.py`, above.

### Bloaters

`notifications.py` at 513 lines is the largest module and mixes message
content, scheduling and rendering. Longest functions: `_render` (73 lines),
`get_free_slots` (67), `send_due_reminders` (63). None are unreadable; all
three would be easier to change if content and layout were separated.

**Long parameter lists.** `offerings.create` takes **11**,
`coffee_chats.create_invite` 8, `mailer.send` 7. These are constructors in
all but name and would read better taking a single object. Not changed:
every caller would move, and the audit is not the moment to churn six files.

### Abusers

**Conditional complexity.** `create_invite` scored C(20),
`public_offerings` C(13). The second was mine -- a price-range summary
written as a conditional expression nested three deep, quicker to rewrite
than to read. *Fixed:* extracted to `_price_range`, and the module now has
nothing above B.

### Couplers

`coffee_notifications.py` imports six underscore-prefixed helpers from
`notifications.py`. Deliberate: two copies of the house email style would
drift, and the first thing to diverge is the footer nobody reads until it is
wrong. It is still reaching into another module's privates, and the honest
fix is to promote those helpers to a shared `email_render` module.

### Inconsistent naming

**The API spoke two languages depending on which handler you hit.** Guest
endpoints returned `camelCase` from hand-built dicts; host endpoints returned
`dict(row)` straight from SQLite, so `guest_email`, `duration_min` and
`price_cents`. Same API, two conventions, decided by accident.

*Fixed:* `coffee_chats.host_view` and `offerings.owner_view` are the single
serialisers, the SPA was updated to match, and the wire is now camelCase
throughout with snake_case confined to the database layer where it belongs.

### Global data

Module-level `bp` blueprints, `log` loggers, and the `_queue` / `_scheduler_lock`
singletons in `mailer` and `notifications`. All deliberate -- one process,
one mail queue, one scheduler -- and all lock-guarded.

### Magic numbers

Few, and mostly harmless (`calendar_logic` bounds-checking hours against 24
and 60). The tunable values that matter -- expiry, nudge interval, nudge cap,
slot multiples -- are already named constants with their reasoning written
above them.

## Bug classes

| Class | Found |
|---|---|
| Syntax | none -- flake8 E9/F63/F7/F82 clean |
| Runtime | **fixed:** the JWT `sub` type, which broke every login in `login_page.py` |
| Logical | **fixed earlier:** `is_slot_free` rejecting every multi-slot booking, reported as "someone just took that slot" |
| Integration | **fixed earlier:** the host received two emails for one booking |
| Portability | **fixed earlier:** `strftime("%-d")` is glibc-only and 500'd on Windows |
| Security | see below |

**Security posture.** Passwords are hashed with Werkzeug; JWTs are HS256 with
an expiry; every sensitive route carries `@token_required` and
`@roles_required`. SQL is parameterised throughout -- no injection surface.

Two things to know. The guest booking token is the only credential on that
path, and the public handlers were written to leak nothing beyond the host
name and free slots; a test asserts that no id, token or internal counter
reaches the guest. And `SECRET_KEY` defaults to a placeholder in
`config.py`, which is right for local development and would be a serious
problem deployed -- it signs every session.

## Maintenance classification

**Corrective** -- the JWT subject bug, the dead assignments, the unused
import.

**Adaptive** -- the PyJWT bug *is* adaptive maintenance arriving late: a
library tightened a rule, one copy of the code adapted, the other did not.
The Postgres path in `database.py` is the other adaptive surface and is
untested here, since the suite runs on SQLite.

**Perfective** -- the long parameter lists and the 513-line
`notifications.py`. Neither affects behaviour; both affect the cost of the
next change.

**Preventive** -- this suite. The most valuable tests are the ones pinning
failures that already happened: `test_a_booking_longer_than_one_slot_is_free`
and `test_a_booking_cannot_straddle_a_taken_slot` both fail against the
original `is_slot_free`.
