"""
Database access layer.

Design goal: the rest of the app (routes/, calendar_logic.py) never touches
sqlite3 or psycopg2 directly. It calls query()/execute() from this module.
That means swapping from local SQLite to a cloud Postgres database is a
one-line environment variable change (DATABASE_URL), not a code change.

- DATABASE_URL starting with "sqlite:///"  -> uses Python's built-in sqlite3.
- DATABASE_URL starting with "postgres://" or "postgresql://" -> uses
  psycopg2 against a cloud Postgres instance (Supabase, Neon, RDS, Render...).

Query text is written once, using "?" placeholders (SQLite style). When the
backend is Postgres, placeholders are translated to "%s" automatically.
"""

import re
import sqlite3
import threading
from contextlib import contextmanager

from flask import g

from config import Config

_IS_POSTGRES = Config.DATABASE_URL.startswith(("postgres://", "postgresql://"))
_local = threading.local()


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------
def _new_connection():
    if _IS_POSTGRES:
        try:
            import psycopg2
            import psycopg2.extras
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "DATABASE_URL points at Postgres but psycopg2-binary is not "
                "installed. Run: pip install psycopg2-binary"
            ) from exc
        conn = psycopg2.connect(Config.DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
        conn.autocommit = False
        return conn
    else:
        path = Config.DATABASE_URL.replace("sqlite:///", "", 1)
        conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def get_db():
    """Return a request-scoped connection (Flask app-context aware)."""
    if g is not None and hasattr(g, "_db_conn"):
        return g._db_conn
    conn = _new_connection()
    if g is not None:
        g._db_conn = conn
    return conn


def close_db(_exc=None):
    conn = g.pop("_db_conn", None)
    if conn is not None:
        conn.close()


@contextmanager
def standalone_connection():
    """Used by scripts (seed_data.py) that run outside a Flask app context."""
    conn = _new_connection()
    try:
        yield conn
    finally:
        conn.close()


def _adapt_sql(sql: str) -> str:
    if _IS_POSTGRES:
        return re.sub(r"\?", "%s", sql)
    return sql


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def query(sql, params=(), one=False, conn=None):
    """SELECT helper. Returns list[dict] or dict|None if one=True."""
    owns_conn = conn is None
    conn = conn or get_db()
    cur = conn.cursor()
    cur.execute(_adapt_sql(sql), params)
    rows = cur.fetchall()
    result = [dict(r) for r in rows]
    cur.close()
    if one:
        return result[0] if result else None
    return result


def execute(sql, params=(), conn=None):
    """INSERT/UPDATE/DELETE helper. Commits and returns last row id (if any)."""
    owns_conn = conn is None
    conn = conn or get_db()
    cur = conn.cursor()
    cur.execute(_adapt_sql(sql), params)
    last_id = None
    if not _IS_POSTGRES:
        last_id = cur.lastrowid
    conn.commit()
    cur.close()
    return last_id


def insert(sql, params=(), conn=None):
    """
    INSERT helper that returns the new row's id on *both* backends.

    sqlite3 exposes it as cursor.lastrowid; Postgres needs an explicit
    "RETURNING id", which is appended here so callers write one query.
    """
    conn = conn or get_db()
    cur = conn.cursor()
    if _IS_POSTGRES:
        cur.execute(_adapt_sql(sql.rstrip().rstrip(";") + " RETURNING id"), params)
        row = cur.fetchone()
        new_id = row[0] if row else None
    else:
        cur.execute(_adapt_sql(sql), params)
        new_id = cur.lastrowid
    conn.commit()
    cur.close()
    return new_id


def rollback(conn=None):
    """
    Abandon the current transaction. Postgres refuses every further statement
    on a connection once one has raised an error, so any `except IntegrityError`
    branch that keeps using the connection must call this first.
    """
    conn = conn or get_db()
    conn.rollback()


def executescript(sql, conn=None):
    conn = conn or get_db()
    if _IS_POSTGRES:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        cur.close()
    else:
        conn.executescript(sql)
        conn.commit()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'provider', 'client')),
    specialty     TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS availability (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day_of_week  INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    slot_minutes INTEGER NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS blocked_slots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    start_time  TEXT,
    end_time    TEXT,
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS appointments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (status IN ('confirmed', 'cancelled', 'completed')),
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_slot
ON appointments (provider_id, date, start_time)
WHERE status = 'confirmed';

CREATE TABLE IF NOT EXISTS email_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    recipient      TEXT NOT NULL,
    subject        TEXT NOT NULL,
    appointment_id INTEGER REFERENCES appointments(id) ON DELETE CASCADE,
    user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'sent', 'failed')),
    error          TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at        TEXT
);

-- Doubles as the de-duplication rule: one message of each kind per person per
-- appointment, so a reminder can never go out twice even if two scans overlap.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_appointment_email
ON email_log (appointment_id, kind, recipient)
WHERE appointment_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS coffee_invites (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    guest_email   TEXT NOT NULL,
    guest_name    TEXT,
    token         TEXT NOT NULL UNIQUE,
    topic         TEXT,
    message       TEXT,
    duration_min  INTEGER NOT NULL DEFAULT 30,
    offering_id   INTEGER REFERENCES offerings(id) ON DELETE SET NULL,
    status        TEXT NOT NULL DEFAULT 'sent'
                  CHECK (status IN ('sent','viewed','booked','declined','expired','revoked')),
    appointment_id INTEGER REFERENCES appointments(id) ON DELETE SET NULL,
    nudge_count   INTEGER NOT NULL DEFAULT 0,
    last_nudge_at TEXT,
    viewed_at     TEXT,
    responded_at  TEXT,
    expires_at    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_coffee_host ON coffee_invites(host_id, status);
CREATE INDEX IF NOT EXISTS idx_coffee_token ON coffee_invites(token);

-- What a provider offers, priced. A provider used to have one free-text
-- `specialty`, which cannot express "I do three different things at three
-- different rates". Money is stored in minor units (cents/pence) as an
-- integer: floats lose money, and 0 is a first-class value meaning free.
CREATE TABLE IF NOT EXISTS offerings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    category     TEXT,
    summary      TEXT,
    description  TEXT,
    duration_min INTEGER NOT NULL DEFAULT 30,
    price_cents  INTEGER NOT NULL DEFAULT 0,
    currency     TEXT NOT NULL DEFAULT 'CAD',
    level        TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_offerings_provider
    ON offerings(provider_id, is_active, sort_order);
"""

SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'provider', 'client')),
    specialty     TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS availability (
    id           SERIAL PRIMARY KEY,
    provider_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day_of_week  INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    start_time   TEXT NOT NULL,
    end_time     TEXT NOT NULL,
    slot_minutes INTEGER NOT NULL DEFAULT 30
);

CREATE TABLE IF NOT EXISTS blocked_slots (
    id          SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    start_time  TEXT,
    end_time    TEXT,
    reason      TEXT
);

CREATE TABLE IF NOT EXISTS appointments (
    id          SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    client_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date        TEXT NOT NULL,
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'confirmed'
                CHECK (status IN ('confirmed', 'cancelled', 'completed')),
    notes       TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_active_slot
ON appointments (provider_id, date, start_time)
WHERE status = 'confirmed';

CREATE TABLE IF NOT EXISTS email_log (
    id             SERIAL PRIMARY KEY,
    kind           TEXT NOT NULL,
    recipient      TEXT NOT NULL,
    subject        TEXT NOT NULL,
    appointment_id INTEGER REFERENCES appointments(id) ON DELETE CASCADE,
    user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    status         TEXT NOT NULL DEFAULT 'queued'
                   CHECK (status IN ('queued', 'sent', 'failed')),
    error          TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    sent_at        TIMESTAMP
);

-- Doubles as the de-duplication rule: one message of each kind per person per
-- appointment, so a reminder can never go out twice even if two scans overlap.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_appointment_email
ON email_log (appointment_id, kind, recipient)
WHERE appointment_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS coffee_invites (
    id            SERIAL PRIMARY KEY,
    host_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    guest_email   TEXT NOT NULL,
    guest_name    TEXT,
    token         TEXT NOT NULL UNIQUE,
    topic         TEXT,
    message       TEXT,
    duration_min  INTEGER NOT NULL DEFAULT 30,
    offering_id   INTEGER REFERENCES offerings(id) ON DELETE SET NULL,
    status        TEXT NOT NULL DEFAULT 'sent'
                  CHECK (status IN ('sent','viewed','booked','declined','expired','revoked')),
    appointment_id INTEGER REFERENCES appointments(id) ON DELETE SET NULL,
    nudge_count   INTEGER NOT NULL DEFAULT 0,
    last_nudge_at TEXT,
    viewed_at     TEXT,
    responded_at  TEXT,
    expires_at    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_coffee_host ON coffee_invites(host_id, status);
CREATE INDEX IF NOT EXISTS idx_coffee_token ON coffee_invites(token);

CREATE TABLE IF NOT EXISTS offerings (
    id           SERIAL PRIMARY KEY,
    provider_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    category     TEXT,
    summary      TEXT,
    description  TEXT,
    duration_min INTEGER NOT NULL DEFAULT 30,
    price_cents  INTEGER NOT NULL DEFAULT 0,
    currency     TEXT NOT NULL DEFAULT 'CAD',
    level        TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SS'))
);
CREATE INDEX IF NOT EXISTS idx_offerings_provider
    ON offerings(provider_id, is_active, sort_order);
"""


def init_db(conn=None):
    schema = SCHEMA_POSTGRES if _IS_POSTGRES else SCHEMA_SQLITE
    executescript(schema, conn=conn)


def init_app(app):
    app.teardown_appcontext(close_db)
