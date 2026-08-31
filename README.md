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
routes/
  auth_routes.py           /api/auth/register, /login, /me
  user_routes.py           /api/providers, /api/admin/users
  availability_routes.py   provider hours + blocked dates, public slot lookup
  appointment_routes.py    book / list / cancel / complete appointments
templates/index.html       SPA shell
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

All routes except `/api/auth/register` and `/api/auth/login` require
`Authorization: Bearer <token>`.

## Notes on production hardening

This is a complete, working reference implementation, not a
production-audited one. Before shipping it publicly, you'd want to add:
rate limiting on `/api/auth/*`, refresh tokens (current tokens just expire
after `JWT_EXP_HOURS`), email verification, HTTPS enforcement, and a
migration tool (e.g. Alembic) instead of the create-if-not-exists schema
in `database.py`.
