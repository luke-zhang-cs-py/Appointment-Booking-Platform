# Code audit

Static analysis (flake8, radon), a 249-test suite, and a coverage report.

```bash
python -m pytest tests/ --cov=. --cov-report=term-missing
python -m flake8 . --select=E9,F63,F7,F82,F401,F811,F841,E722 --exclude=.git
python -m radon cc . -s -n C --exclude ".git/*,tests/*"
```

The first pass of this audit fixed what was cheap and listed what was not.
This is the second pass, which went back and did the listed part: the long
parameter lists, the 513-line module, the cross-module import of private
helpers, and the signing key. Everything under **Findings** is now *fixed*
rather than *noted*, and the four bugs found on the way down are in
**Bugs found by the refactor**.

## Coverage

**249 tests, 93%.** It was 82 tests and 73% after the first pass, and 0%
before it.

| Module | Cover | | Module | Cover |
|---|---|---|---|---|
| `config.py` | **100%** | | `routes/appointment_routes.py` | 93% |
| `email_render.py` | **100%** | | `scheduler.py` | 93% |
| `offerings.py` | **100%** | | `notifications.py` | 93% |
| `routes/auth_routes.py` | **100%** | | `routes/coffee_routes.py` | 92% |
| `routes/availability_routes.py` | **100%** | | `app.py` | 91% |
| `routes/email_routes.py` | **100%** | | `coffee_chats.py` | 90% |
| `auth.py` | 98% | | `coffee_notifications.py` | 89% |
| `calendar_logic.py` | 97% | | `mailer.py` | 86% |
| `routes/user_routes.py` | 97% | | `database.py` | 81% |

The three worst modules last time were `routes/appointment_routes.py` (46%),
`notifications.py` (48%) and `routes/email_routes.py` (45%). Those were the
oldest code in the project and the least looked at, which is the combination
worth testing first — and two of them hold permission checks, and a
permission check with no test is a permission check nobody has run.

What is still uncovered is deliberate: the Postgres half of `database.py`
(the suite runs on SQLite), the SMTP half of `mailer.py` (there is no mail
server in a test), and `app.py`'s `__main__` block.

## Bugs found by the refactor

**1. `login_page.py` could not log anybody in.** *(found in the first pass,
repeated here because it explains the rest)* It built a JWT with an integer
`sub`. RFC 7519 says that is a string and PyJWT ≥ 2.10 enforces it on
decode, so the standalone server issued tokens it then rejected. `auth.py`
had the fix and a comment explaining it; the copy never got it.

**2. `datetime.utcnow()` in `auth.py` and `login_page.py`.** Deprecated and
scheduled for removal, and it returns a naive datetime that only happens to
mean UTC. The same shape of problem as the one above: a library moved, and
this code has not. Now `datetime.now(timezone.utc)`; PyJWT converts aware
datetimes itself, so nothing changes on the wire.

**3. A console that cannot draw a dash marked every email as failed.** With
no `SMTP_HOST` — the documented local-development default — `mailer` prints
the message instead of sending it. The details block puts an en dash between
a start and an end time. Under `cp437` or a bare C locale, printing one
raises `UnicodeEncodeError`, `_process` catches it, and the email is
recorded as **failed** in `email_log`, in the mode where "sending" only ever
meant printing. Now degrades to whatever the console can show.
`test_the_failure_is_real_without_the_guard` pins the underlying encode.

**4. `login_page.py` looked users up by a string id.** Once `sub` became a
string, `WHERE id = ?` was being handed `'3'` against an INTEGER column.
SQLite coerces it and matches, so it worked by luck; `auth.py` had always
converted explicitly. Now both do.

**5. Two dead locals in `public_offerings`**, left over when `_price_range`
was extracted in the first pass. Flake8 F841 caught them the moment the file
was touched again.

## Findings

### Bloaters

**`notifications.py` was 513 lines** because it was doing three jobs:
deciding what to say, deciding how it should look, and deciding when to wake
up. Split into three modules that each answer one question.

| | Lines | Answers |
|---|---|---|
| `notifications.py` | 320 | what Almanac says, and on what occasion |
| `email_render.py` | 206 | how a message is laid out and how a date is worded |
| `scheduler.py` | 93 | when the background sweep runs |

**Long parameter lists.** All three are now one object each.

| Was | Now |
|---|---|
| `mailer.send(kind, to, subject, text, html, appointment_id, user_id)` | `mailer.send(Message)` |
| `offerings.create(provider_id, title, duration_min, price_cents, currency, category, summary, description, level, sort_order, conn)` | `offerings.create(provider_id, OfferingDraft, conn)` |
| `coffee_chats.create_invite(host_id, guest_email, guest_name, topic, message, duration_min, expiry_days, offering_id)` | `coffee_chats.create_invite(host_id, InviteRequest)` |

Eleven arguments in a row is a thing pretending to be a signature: they
always travel together, none of them means anything alone, and every call
site was already writing them in the same order and hoping. Both new classes
carry a `from_payload` classmethod, which is where the API shape (camelCase,
strings out of JSON) stops and the domain shape starts — so the two route
handlers that used to do ten lines of `body.get()` and integer conversion
now do one line and catch one kind of error instead of two.

`offerings.update` derives its patchable columns from `OfferingDraft`'s
fields rather than repeating them, so adding a field cannot leave a column
creatable but not editable.

### Couplers

**`coffee_notifications.py` imported six underscore-prefixed helpers from
`notifications.py`.** It worked, and it was still one module reading
another's privates — the import was load-bearing while the underscore said
"do not depend on this". Those helpers were never private in spirit, only in
name. They are now public in `email_render.py`, a module whose whole purpose
is to be shared, and the two notification modules are peers using it rather
than one burrowing into the other. The one genuinely appointment-shaped
helper, `_load`, is now `notifications.load_appointment`.

### Abusers

**Conditional complexity.** Nothing outside `login_page.py` now scores above
B. `create_invite` was C(20) — its validation moved onto `InviteRequest`,
and the offering lookup and duplicate check became named helpers. `book` was
C(14), and its guard chain is now `_open_invite_for` and `_parse_start`.
`get_free_slots` was C(14) doing four things in one function; it is now
`_windows_for`, `_busy_ranges` and `_tile`. `auth_routes.register` was C(14)
— four validate-and-return pairs wrapped around the part that registers
somebody, now `_registration_problem`.

**A string sentinel used as a permission result.**
`_load_owned_appointment` returned an appointment, or `None`, or the literal
string `"forbidden"`, and both callers compared a dict against it. Three
kinds of thing out of one return, and nothing in the signature says a caller
has to check for a magic string — missing it fails *open*, which for a
permission check is the wrong direction. Now `_load` and `_may_act_on`, and
`test_a_stranger_cannot_cancel` covers it.

### Dispensables

Dead assignments in `database.py` and `routes/offering_routes.py`, an unused
import in `coffee_chats.py`. All removed; flake8 F401/F811/F841 is clean
across the project.

### Inconsistent naming

**The API used to speak two languages depending on which handler you hit.**
Guest endpoints returned camelCase from hand-built dicts; host endpoints
returned `dict(row)` straight from SQLite, so `guest_email`, `duration_min`,
`price_cents`. Fixed in the first pass: `coffee_chats.host_view` and
`offerings.owner_view` are the single serialisers, and snake_case is now
confined to the database layer.

### Magic numbers

The tunable values that matter — expiry, nudge interval, nudge cap, slot
multiples — were already named constants with the reasoning written above
them. This pass named the rest that had crept in: `DEFAULT_DURATION_MIN`,
`DEFAULT_CURRENCY`, `MIN_PASSWORD_LENGTH`, `MIN_SECRET_BYTES`,
`MIN_INTERVAL_SECONDS`, `STARTUP_DELAY_SECONDS`, `MINUTES_IN_A_DAY`.

### Global data

Module-level `bp` blueprints, `log` loggers, and the `_queue` /
`_scheduler_lock` singletons in `mailer` and `scheduler`. All deliberate —
one process, one mail queue, one timer — and all lock-guarded.

## Security

**`SECRET_KEY` no longer has a usable production default.** It signs every
session token, and the development fallback is committed, so it is public: a
deployment that kept it was one where anyone could mint a valid admin token.
Local development still needs zero setup, so the rule is that the
placeholder is fine while `DEBUG` is on and **fatal** when it is not —
`config.check_secret_key` raises at boot rather than serving forgeable
tokens. The same check enforces a 32-byte floor, because HS256 with a key
shorter than the SHA-256 digest is weaker than the algorithm it names. (That
also removed 223 `InsecureKeyLengthWarning`s from the test run, which were
burying everything else the suite had to say.)

Otherwise unchanged and re-verified: passwords hashed with Werkzeug, JWTs
HS256 with an expiry, `@token_required` / `@roles_required` on every
sensitive route, SQL parameterised throughout. Two tests worth naming:
`test_nobody_registers_themselves_as_an_admin`, since admin is the one role
that can change every other, and
`test_guest_page_needs_no_auth_and_hides_internals`, since a leaked invite
link should cost one coffee chat rather than read access to a calendar.

New: `test_guest_text_reaches_the_html_escaped`. A host types the guest's
name and it lands in an HTML email, so `email_render.escape` is the only
thing between that and a mail client.

## Bug classes

| Class | Found |
|---|---|
| Syntax | none — flake8 E9/F63/F7/F82 clean |
| Runtime | **fixed:** the JWT `sub` type, which broke every login in `login_page.py` |
| Logical | **fixed:** `is_slot_free` rejecting every multi-slot booking, reported as "someone just took that slot" |
| Integration | **fixed:** the host received two emails for one booking |
| Portability | **fixed:** `strftime("%-d")` is glibc-only; and the en dash that failed on a narrow console |
| Security | **fixed:** the placeholder signing key now refuses to boot in production |

## Maintenance classification

**Corrective** — the JWT subject type, the string-id lookup, the dead
assignments, the unused import.

**Adaptive** — `utcnow()`, and the PyJWT `sub` rule before it. Both are the
same story: a library tightened, one copy of the code adapted, the other did
not. This is the argument against `login_page.py` existing at all, and it
stays only because it is documented as runnable alone with its own database
— its complexity is left as-is, since churning a standalone demo for style
is how the divergence that caused these bugs starts.

**Perfective** — the three-way split of `notifications.py`, the three
parameter objects, the complexity extractions. None of it changes behaviour;
all of it changes the cost of the next change. The 249 tests are what made
it safe to do: every one of them passed before the refactor started and
after it finished.

**Preventive** — the suite, and specifically the tests that pin failures
that already happened: `test_a_booking_longer_than_one_slot_is_free` and
`test_a_booking_cannot_straddle_a_taken_slot` both fail against the original
`is_slot_free`; `test_the_host_is_not_told_twice_about_a_coffee_chat` fails
against the original booking path; `test_production_refuses_to_start` fails
against the original `config.py`.
