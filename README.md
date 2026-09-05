# Almanac — Multi-Role Appointment Booking Platform

A full-stack scheduling app: Flask + JWT auth on the backend, a vanilla
HTML/CSS/JS single-page frontend served by the same app. Three roles —
**client**, **provider**, **admin** — each get their own dashboard.

## Features

- **JWT authentication** — register/login, tokens signed with `HS256`,
  password hashing via Werkzeug's `generate_password_hash`.
- **Role-based access control** — `@token_required` + `@roles_required(...)`
  decorators guard every sensitive endpoint.
- **Calendar logic** (`calendar_logic.py`) — turns a provider's recurring
  weekly hours, one-off blocked dates, and existing bookings into a live
  list of free slots for any date; filters out past times for "today";
  a unique DB constraint blocks race-condition double-bookings.
- **Automatic email notifications** (`mailer.py` + `notifications.py`) — welcome,
  booking confirmation, cancellation, completion, and a 24-hour reminder, sent
  without anyone pressing a button. Delivery happens on a background thread so
  a slow mail server never slows down a booking, every attempt is recorded in
  an `email_log` table, and a unique index over that log guarantees nobody is
  mailed the same thing twice.
- **Coffee chats** (`coffee_chats.py` + `coffee_notifications.py`) — the
  reverse direction: an email that *produces* a booking. Send someone an
  invite, they click the link, they see your real availability, they pick a
  time. **No account needed** — a coffee chat is usually first contact, and
  asking a founder or an alum to register before they can pick a slot loses
  most of them. Follow-ups go out automatically after three days (capped at
  two, because a third is pestering), invites expire on their own, and a
  booked one becomes an ordinary appointment with the usual confirmation and
  24-hour reminder.
- **Priced offerings** (`offerings.py`) — a provider lists several things
  they do, each with its own length, price and description, instead of one
  free-text `specialty`. A coffee chat invite can name one, and the session
  takes its duration and topic from the catalogue entry so the two cannot
  drift apart. Money is stored in minor units as an integer; `0` means free
  and is a real answer, not a missing one.
- **Database layer built for the cloud** — runs on local SQLite with zero
  setup, and switches to a managed cloud Postgres database (Supabase, Neon,
  Render, Railway, AWS RDS...) by changing one environment variable
  (`DATABASE_URL`) — no code changes.

## Project layout

```
app.py                     Flask app factory, blueprint registration
config.py                  Reads all settings from environment variables
database.py                DB abstraction: SQLite locally, Postgres in the cloud
auth.py                    JWT creation/verification, RBAC decorators
calendar_logic.py          Free-slot calculation engine
mailer.py                  Email transport: SMTP, background queue, delivery log
notifications.py           What gets mailed and when + the reminder scheduler
coffee_chats.py            Invite lifecycle, tokens, guest booking
coffee_notifications.py    Invite / nudge / booked / declined emails
offerings.py               Priced session catalogue per provider
seed_luke.py               Seeds a provider with availability + catalogue
routes/
  auth_routes.py           /api/auth/register, /login, /me
  user_routes.py           /api/providers, /api/admin/users
  availability_routes.py   provider hours + blocked dates, public slot lookup
  appointment_routes.py    book / list / cancel / complete appointments
  email_routes.py          admin delivery log, test send, manual reminder run
  coffee_routes.py         invites (host, authed) + booking (guest, token)
  offering_routes.py       catalogue CRUD (owner) + public browse
templates/index.html       SPA shell
templates/coffee.html      Guest booking page — no login, one decision
static/architecture.html   Visual overview of every component (open it in a browser)
static/css/style.css       Design system ("departure board" visual identity)
static/js/api.js           Fetch wrapper (JWT storage + auth headers)
static/js/app.js           SPA routing + role-specific dashboard rendering
seed_data.py                Creates a starter admin account
requirements.txt
.env.example
```

## Run it locally

```bash
pip install -r requirements.txt
python seed_data.py        # creates admin@almanac.local / admin12345
python app.py               # http://localhost:5000
```

No `.env` needed to start — sensible defaults kick in (SQLite file,
dev JWT secret). For anything beyond local testing, copy `.env.example` to
`.env`, fill in a real `SECRET_KEY`, and load it before running.

Sign up as a **client** or **provider** from the UI, or sign in as the
seeded admin. A provider needs to add weekly hours under **My schedule**
before clients can book them.

For a map of the whole system — every file, the email pipeline, the schema,
the API surface — open `static/architecture.html` in a browser (double-click
it, or visit `/static/architecture.html` while the app is running).

## Moving to a cloud database

1. Provision a Postgres database (Supabase, Neon, Railway, Render, or
   AWS RDS all work).
2. `pip install psycopg2-binary` (already listed in `requirements.txt`).
3. Set `DATABASE_URL=postgres://user:password@host:5432/dbname`.
4. Run `python app.py` — `database.py` detects the `postgres://` prefix
   and switches backends automatically; tables are created on first boot.

Deploying to a host like Render/Railway/Fly.io: set `SECRET_KEY`,
`DATABASE_URL`, and `PORT` as environment variables in the platform's
dashboard, then point it at `python app.py` (or run under `gunicorn app:app`
for production instead of Flask's dev server).

## API summary

| Method | Path                                    | Who            |
|--------|------------------------------------------|----------------|
| POST   | `/api/auth/register`                     | anyone         |
| POST   | `/api/auth/login`                        | anyone         |
| GET    | `/api/auth/me`                           | any logged-in  |
| GET    | `/api/providers`                         | any logged-in  |
| GET    | `/api/providers/<id>/slots?date=YYYY-MM-DD` | any logged-in |
| GET/POST | `/api/availability/mine`               | provider       |
| DELETE | `/api/availability/mine/<id>`            | provider       |
| POST   | `/api/availability/mine/block`           | provider       |
| DELETE | `/api/availability/mine/block/<id>`      | provider       |
| POST   | `/api/appointments`                      | client         |
| GET    | `/api/appointments/mine`                 | any logged-in (scoped by role) |
| POST   | `/api/appointments/<id>/cancel`          | owner or admin |
| POST   | `/api/appointments/<id>/complete`        | provider/admin |
| GET    | `/api/admin/users`                       | admin          |
| PATCH  | `/api/admin/users/<id>`                  | admin          |
| GET    | `/api/admin/emails`                      | admin          |
| POST   | `/api/admin/emails/test`                 | admin          |
| POST   | `/api/admin/emails/run-reminders`        | admin          |

All routes except `/api/auth/register` and `/api/auth/login` require
`Authorization: Bearer <token>`.

## Email notifications

Five messages go out on their own, no button required:

| When                                   | Who gets mailed        |
|----------------------------------------|------------------------|
| An account is created                  | the new user           |
| A client books a slot                  | client **and** provider |
| An appointment is cancelled            | client **and** provider |
| A provider marks an appointment done   | the client             |
| `REMINDER_HOURS_BEFORE` the start time | client **and** provider |

**Locally there is nothing to configure.** With `SMTP_HOST` unset, each message
is printed to the terminal running `python app.py` — you can book an
appointment and watch the confirmation appear. Set `SMTP_HOST`, `SMTP_USERNAME`,
and `SMTP_PASSWORD` (SendGrid, Mailgun, Postmark, SES, Gmail app password…) and
the identical messages start being delivered. No extra pip package: it's
`smtplib` from the standard library.

Every message is multipart — plain text plus a styled HTML version — and both
halves are generated from one description of the message, so they can't drift
apart.

Signed in as an admin, **Email log** in the sidebar shows what has been sent,
to whom, and whether it landed, with a **Send test** button for checking SMTP
credentials and **Run reminders now** for forcing a scan.

How it holds up:

- **Nothing blocks on the mail server.** `mailer.send()` writes a row and hands
  the message to a background worker thread; the booking request returns
  immediately. A refused SMTP connection is logged as a failed row, never a 500.
- **Nobody is mailed twice.** A unique index on
  `email_log (appointment_id, kind, recipient)` is the source of truth, so
  overlapping reminder scans, a double-clicked button, or several web workers
  running at once all collapse to one message.
- **Failures are retried.** A row left in `failed` is picked up by the next
  reminder scan.

Reminders run from a background thread every `REMINDER_SCAN_MINUTES`. If you
prefer a real scheduler, set `REMINDERS_ENABLED=0` and point cron at
`POST /api/admin/emails/run-reminders` instead.

## Notes on production hardening

This is a complete, working reference implementation, not a
production-audited one. Before shipping it publicly, you'd want to add:
rate limiting on `/api/auth/*`, refresh tokens (current tokens just expire
after `JWT_EXP_HOURS`), email verification, HTTPS enforcement, and a
migration tool (e.g. Alembic) instead of the create-if-not-exists schema
in `database.py`. On the email side: an unsubscribe link and a per-user
notification preference, SPF/DKIM records for whatever domain you send from,
and — once one process is no longer enough — moving the reminder scan out of
the app thread and into cron or a task queue.

## Coffee chat flow

```
host sends invite ──► guest gets an email with a tokenised link
                          │
                          ├─ opens it        → invite marked "viewed"
                          ├─ picks a slot    → real appointment + confirmations
                          ├─ declines        → host told, nothing held
                          └─ silence         → one nudge after 3 days, then expiry
```

| Endpoint | Who | Purpose |
|---|---|---|
| `POST /api/coffee/invites` | host | create and send an invite |
| `GET /api/coffee/invites` | host | list invites + conversion stats |
| `POST /api/coffee/invites/<id>/nudge` | host | manual follow-up |
| `DELETE /api/coffee/invites/<id>` | host | revoke |
| `GET /coffee/<token>` | guest | booking page, no login |
| `GET /api/coffee/public/<token>` | guest | invite details + free slots |
| `POST /api/coffee/public/<token>/book` | guest | take a slot |
| `POST /api/coffee/public/<token>/decline` | guest | say no |

The token is the only credential on the guest side, so the public handlers
return the host's name and free slots and nothing else — no ids, no token
echo, no nudge counts. A leaked link should cost one coffee chat, not read
access to a calendar.

A guest who books gets a `users` row so `appointments.client_id` has
something real to point at, created with an unusable password hash: mailable
and bookable, unable to log in. If they later register properly the address
already exists and their history comes with them.

## Offerings and the slot grid

```bash
python seed_luke.py            # provider + a week of hours + 14 sessions
python seed_luke.py --list     # show the catalogue
python seed_luke.py --reset    # rebuild the catalogue from CATALOGUE
```

| Endpoint | Who | Purpose |
|---|---|---|
| `GET /api/providers/<id>/offerings` | anyone | browse the catalogue, grouped |
| `GET /api/offerings/mine` | owner | list, including deactivated |
| `POST /api/offerings/mine` | owner | create |
| `PATCH /api/offerings/mine/<id>` | owner | edit title, price, duration… |
| `DELETE /api/offerings/mine/<id>` | owner | deactivate (never deletes) |

**A booking must begin and end on a slot boundary, so the provider's grid
size decides which session lengths are bookable at all.** On a 30-minute
grid a 45-minute session has no valid start time anywhere in the day — not
rare, impossible. `seed_luke.py` therefore sets a 15-minute grid, which
divides every duration in the catalogue, and the guest page asks for start
times that can hold the whole session rather than raw slots.
