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
