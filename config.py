import logging
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

log = logging.getLogger("almanac.config")

# The development fallback for SECRET_KEY. It is committed, so it is public.
# check_secret_key() below refuses to start a non-debug process that is still
# using it -- see the note there for why that is worth a hard failure.
DEV_SECRET_KEY = "dev-secret-change-me-in-production"

# HS256 signs with a SHA-256 HMAC, so a key shorter than the 32-byte digest
# is weaker than the algorithm it claims to be. PyJWT >= 2.10 warns about it
# on every encode; this is the same floor, enforced once at startup.
MIN_SECRET_BYTES = 32


class Config:
    """
    All values are read from environment variables so the exact same code
    runs locally (SQLite file) and in the cloud (managed Postgres) --
    you only ever change environment variables, never code.
    """

    # --- Auth -----------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY") or DEV_SECRET_KEY
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
    # Loopback by default. It used to bind 0.0.0.0, which with DEBUG on --
    # also the default -- put the Werkzeug debugger, an interactive Python
    # console, on every interface of the machine. Anyone on the same network
    # could run code as this process. Set HOST=0.0.0.0 deliberately when you
    # actually want that, and turn DEBUG off when you do.
    HOST = os.environ.get("HOST", "127.0.0.1")
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


def secret_key_problem(secret_key):
    """What is wrong with this SECRET_KEY, in a sentence, or None if nothing.

    SECRET_KEY signs every session token this app issues. Two ways it goes
    wrong, and neither announces itself: the placeholder above, which is in a
    public repository and lets anyone mint a valid admin token, and a key
    short enough that HS256 is not delivering the strength it names.
    """
    if secret_key == DEV_SECRET_KEY:
        return ("SECRET_KEY is still the development placeholder committed in "
                "config.py. It signs every session token and it is public. "
                "Generate one with: "
                "python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
    size = len((secret_key or "").encode("utf-8"))
    if size < MIN_SECRET_BYTES:
        return (f"SECRET_KEY is {size} bytes. HS256 needs at least "
                f"{MIN_SECRET_BYTES} to be worth the name.")
    return None


def check_secret_key(secret_key, debug):
    """Warn in development, refuse to start anywhere else.

    Local development needs zero setup -- that is the point of every default
    in this file -- so the placeholder stays usable while DEBUG is on. The
    moment it is off, this is a deployment, and a deployment signing sessions
    with a published string is not a warning-level problem. Failing at
    startup is loud; a quietly forgeable token is not.

    Returns the problem text (having logged it) or None.
    """
    problem = secret_key_problem(secret_key)
    if problem is None:
        return None
    if not debug:
        raise RuntimeError(problem)
    log.warning("%s Fine locally, fatal with FLASK_DEBUG=0.", problem)
    return problem
