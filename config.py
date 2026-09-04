import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    """
    All values are read from environment variables so the exact same code
    runs locally (SQLite file) and in the cloud (managed Postgres) --
    you only ever change environment variables, never code.
    """

    # --- Auth -----------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")
    JWT_EXP_HOURS = int(os.environ.get("JWT_EXP_HOURS", "24"))
    JWT_ALGORITHM = "HS256"

    # --- Database ---------------------------------------------------------
    # Local default: a SQLite file next to this project (zero setup).
    # Cloud: set DATABASE_URL to a Postgres connection string, e.g. one
    # issued by Supabase / Neon / Render / Railway / AWS RDS:
    #   postgres://user:password@host:5432/dbname
    # No code changes are required to switch -- see database.py.
    DATABASE_URL = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "appointments.db")
    )

    # --- Server -----------------------------------------------------------
    PORT = int(os.environ.get("PORT", "5000"))
    DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"

    # --- Email ------------------------------------------------------------
    # Leave SMTP_HOST empty (the default) and every message is written to the
    # server log instead of being handed to a real mail server -- so local
    # development needs zero setup, exactly like the SQLite default above.
    # Point SMTP_HOST at a provider (SendGrid, Mailgun, Postmark, Amazon SES,
    # Gmail...) and the same code starts delivering for real.
    MAIL_ENABLED = os.environ.get("MAIL_ENABLED", "1") == "1"
    MAIL_FROM = os.environ.get("MAIL_FROM", "Almanac <no-reply@almanac.local>")
    MAIL_REPLY_TO = os.environ.get("MAIL_REPLY_TO", "")

    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "1") == "1"  # STARTTLS, port 587
    SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "0") == "1"  # implicit TLS, port 465
    SMTP_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "20"))

    # Used to build links inside emails ("View your appointments").
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")

    # --- Automatic reminders ----------------------------------------------
    # A background thread scans for appointments starting within
    # REMINDER_HOURS_BEFORE and mails both parties, once each.
    REMINDERS_ENABLED = os.environ.get("REMINDERS_ENABLED", "1") == "1"
    REMINDER_HOURS_BEFORE = float(os.environ.get("REMINDER_HOURS_BEFORE", "24"))
    REMINDER_SCAN_MINUTES = float(os.environ.get("REMINDER_SCAN_MINUTES", "15"))
