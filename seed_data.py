"""
Creates a starter admin account so there's a way into the admin views on a
brand-new database. Run once with:  python seed_data.py
"""

import database as db
from auth import hash_password

ADMIN_EMAIL = "admin@almanac.local"
ADMIN_PASSWORD = "admin12345"


def main():
    with db.standalone_connection() as conn:
        db.init_db(conn=conn)
        existing = db.query("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,), one=True, conn=conn)
        if existing:
            print(f"Admin already exists: {ADMIN_EMAIL}")
            return
        db.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            ("Platform Admin", ADMIN_EMAIL, hash_password(ADMIN_PASSWORD), "admin"),
            conn=conn,
        )
        print("Created admin account:")
        print(f"  email:    {ADMIN_EMAIL}")
        print(f"  password: {ADMIN_PASSWORD}")
        print("Sign in, then change the password by registering a new admin and deactivating this one.")


if __name__ == "__main__":
    main()
