# Contributing

## Setup

```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5003
```

Zero configuration by design. With no `.env` and no `DATABASE_URL` the app
runs on local SQLite and the mailer writes messages to the console instead of
sending them, so a clone is immediately runnable. `psycopg2-binary` in
`requirements.txt` is only needed if you point `DATABASE_URL` at a managed
Postgres database.

## Configuration

Everything is read from the environment, never committed. `.env` is
gitignored, and so is `*.db`.

| Variable | Default if unset |
|---|---|
| `DATABASE_URL` | local SQLite at `DB_PATH` |
| `SECRET_KEY` | a development key — **set this in production** |
| `MAIL_ENABLED` | off; messages go to the console |
| `SMTP_HOST` / `SMTP_PASSWORD` | unset; nothing is sent |
| `REMINDERS_ENABLED` | the 24-hour reminder scan |
| `APP_BASE_URL` | used to build coffee-chat invite links |

That unconfigured state is the one CI runs, and it is worth keeping runnable:
a test that needs a real SMTP host or a real secret is either skipped or is a
bug.

## Tests

```bash
pytest -q
python -m flake8 . --select=E9,F63,F7,F82
```

303 tests, about 70 seconds. Both must be clean before a push.

Two things the suite guards that are easy to break by accident:

- **Times are one language on the wire.** Timestamps are written and parsed
  through `database.TIMESTAMP_FORMAT`. Three different spellings of the same
  instant once made the reminder scan fire early.
- **Nobody is mailed the same thing twice.** A unique index over `email_log`
  enforces it; if you add a notification, it needs a log key.

## Conventions

Validation returns a problem rather than raising: a window with
`slot_minutes: 0` used to be accepted and then loop forever, and a block with
a start but no end used to be a 500. Both are now refused at the edge, and
where a block cannot be read the fail-safe is to treat the whole day as
blocked rather than bookable.
