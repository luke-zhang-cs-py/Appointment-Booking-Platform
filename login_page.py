"""
login_page.py — a single-file, self-contained login/register server.

Run it on its own:
    pip install flask pyjwt werkzeug
    python login_page.py
    -> open http://localhost:5000

It creates its own SQLite file (login_users.db) next to this script and
exposes /api/auth/register, /api/auth/login, /api/auth/me — the same
shape used by the full Almanac appointment app, so if you point it at
that project's appointments.db it will recognize the same accounts.
"""

import datetime
import os
import re
import sqlite3

import jwt
from flask import Flask, Response, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

# ----------------------------------------------------------------------
# Config — edit these three lines for your setup
# ----------------------------------------------------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me-in-production")
JWT_EXP_HOURS = 24
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "login_users.db"))

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ALLOWED_ROLES = ("client", "provider")

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY


# ----------------------------------------------------------------------
# Database (SQLite, single table, zero setup)
# ----------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL CHECK (role IN ('admin', 'provider', 'client')),
            specialty     TEXT,
            is_active     INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Auth helpers
# ----------------------------------------------------------------------
def create_token(user):
    # Aware rather than utcnow(): that call is deprecated and scheduled for
    # removal. Same change as auth.py -- this file is a standalone copy, and
    # the last thing it missed was the "sub" fix directly below.
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        # RFC 7519 says "sub" is a string and PyJWT >= 2.10 enforces it on
        # decode, so an int here encodes fine and then fails every login with
        # InvalidSubjectError. auth.py carries the same fix; this file is a
        # standalone copy and did not get it until it was audited.
        "sub": str(user["id"]),
        "role": user["role"],
        "name": user["name"],
        "iat": now,
        "exp": now + datetime.timedelta(hours=JWT_EXP_HOURS),
    }
    return jwt.encode(payload, app.config["SECRET_KEY"], algorithm="HS256")


def public_user(user):
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],
        "specialty": user["specialty"],
    }


# ----------------------------------------------------------------------
# API routes
# ----------------------------------------------------------------------
@app.post("/api/auth/register")
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = data.get("role", "client")
    specialty = (data.get("specialty") or "").strip() or None

    if not name or not email or not password:
        return jsonify({"error": "Name, email, and password are all required"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "That email address doesn't look valid"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password needs to be at least 8 characters"}), 400
    if role not in ALLOWED_ROLES:
        return jsonify({"error": "Role must be 'client' or 'provider'"}), 400

    db = get_db()
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        return jsonify({"error": "An account with that email already exists"}), 409

    cur = db.execute(
        "INSERT INTO users (name, email, password_hash, role, specialty) VALUES (?, ?, ?, ?, ?)",
        (name, email, generate_password_hash(password), role, specialty),
    )
    db.commit()
    user = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify({"token": create_token(user), "user": public_user(user)}), 201


@app.post("/api/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Incorrect email or password"}), 401
    if not user["is_active"]:
        return jsonify({"error": "This account has been deactivated"}), 403

    return jsonify({"token": create_token(user), "user": public_user(user)})


@app.get("/api/auth/me")
def me():
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return jsonify({"error": "Missing authorization token"}), 401
    try:
        payload = jwt.decode(header[7:].strip(), app.config["SECRET_KEY"], algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Token expired, please log in again"}), 401
    except jwt.InvalidTokenError:
        return jsonify({"error": "Invalid token"}), 401

    db = get_db()
    # "sub" is a string by the time it comes back out (see create_token).
    # SQLite would coerce it against an INTEGER column and match anyway;
    # converting here says what is meant instead of relying on that.
    user = db.execute("SELECT * FROM users WHERE id = ?",
                      (int(payload["sub"]),)).fetchone()
    if not user:
        return jsonify({"error": "Account not found"}), 401
    return jsonify({"user": public_user(user)})


# ----------------------------------------------------------------------
# The page itself — HTML/CSS/JS embedded as one string, served directly
# (no Jinja templating involved, so the JS template literals below are
# completely safe from server-side escaping).
# ----------------------------------------------------------------------
PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Almanac — Sign in</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500..800&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
  :root {
    --ink: #12161d; --panel: #1b2230; --panel-alt: #232c3d; --border: #2c3547;
    --parchment: #ece6d6; --parchment-dim: #c9c3b2; --muted: #8d93a3;
    --brass: #d9a441; --brass-strong: #f0bd5c; --teal: #4fa396; --teal-strong: #6fc0b3;
    --clay: #c1573f; --clay-strong: #dd7057;
    --font-display: 'Fraunces', serif; --font-body: 'Inter', system-ui, sans-serif; --font-mono: 'IBM Plex Mono', monospace;
    --radius-s: 6px; --radius-m: 10px; --radius-l: 16px;
    --shadow-card: 0 10px 30px -12px rgba(0, 0, 0, 0.55);
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0; background: var(--ink); color: var(--parchment);
    font-family: var(--font-body); min-height: 100vh;
    background-image: radial-gradient(circle at 15% 0%, rgba(217, 164, 65, 0.06), transparent 45%),
                       radial-gradient(circle at 85% 100%, rgba(79, 163, 150, 0.06), transparent 45%);
  }
  button, input { font-family: inherit; }
  ::selection { background: var(--brass); color: var(--ink); }
  button:focus-visible, input:focus-visible { outline: 2px solid var(--brass-strong); outline-offset: 2px; }
  .hidden { display: none !important; }
  #auth-screen { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 2rem 1rem; }
  .brand { display: flex; align-items: baseline; gap: 0.5rem; }
  .brand .mark { font-family: var(--font-display); font-weight: 700; font-size: 1.4rem; color: var(--brass-strong); }
  .brand .mark::after { content: '·'; color: var(--teal-strong); margin-left: 2px; }
  .brand.sub { font-family: var(--font-mono); font-size: 0.68rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.14em; }
  .ledger { width: min(880px, 100%); display: grid; grid-template-columns: 1fr 1.1fr; border-radius: var(--radius-l); overflow: hidden; box-shadow: var(--shadow-card); border: 1px solid var(--border); }
  .ledger-cover { background: linear-gradient(160deg, #1a2130, #0e1218 85%); padding: 2.75rem 2.25rem; display: flex; flex-direction: column; justify-content: space-between; position: relative; border-right: 1px dashed var(--border); }
  .ledger-cover::before { content: ''; position: absolute; inset: 14px; border: 1px solid rgba(217, 164, 65, 0.18); border-radius: 10px; pointer-events: none; }
  .ledger-cover h1 { font-family: var(--font-display); font-size: 2.1rem; line-height: 1.15; margin: 1.5rem 0 0.75rem; color: var(--parchment); }
  .ledger-cover p { color: var(--parchment-dim); font-size: 0.95rem; line-height: 1.55; max-width: 30ch; }
  .ledger-cover .roles { display: flex; flex-direction: column; gap: 0.6rem; margin-top: 2rem; font-family: var(--font-mono); font-size: 0.75rem; color: var(--muted); }
  .ledger-cover .roles span.tag { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 0.5rem; }
  .ledger-form { background: var(--panel); padding: 2.75rem 2.5rem; display: flex; flex-direction: column; }
  .tabs { display: flex; gap: 1.5rem; margin-bottom: 1.75rem; border-bottom: 1px solid var(--border); }
  .tab { background: none; border: none; color: var(--muted); font-size: 0.95rem; font-weight: 600; padding: 0.5rem 0 0.9rem; cursor: pointer; position: relative; }
  .tab.active { color: var(--parchment); }
  .tab.active::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1px; height: 2px; background: var(--brass-strong); }
  .field { margin-bottom: 1.1rem; }
  .field label { display: block; font-size: 0.78rem; color: var(--muted); margin-bottom: 0.4rem; text-transform: uppercase; letter-spacing: 0.06em; }
  .field input { width: 100%; background: var(--panel-alt); border: 1px solid var(--border); border-radius: var(--radius-s); color: var(--parchment); padding: 0.65rem 0.75rem; font-size: 0.95rem; }
  .field input:focus { border-color: var(--brass); }
  .role-picker { display: flex; gap: 0.6rem; }
  .role-picker label { flex: 1; border: 1px solid var(--border); border-radius: var(--radius-s); padding: 0.55rem 0.5rem; text-align: center; font-size: 0.82rem; cursor: pointer; color: var(--muted); }
  .role-picker input { display: none; }
  .role-picker label:has(input:checked) { border-color: var(--brass); background: rgba(217,164,65,0.08); color: var(--parchment); }
  .btn { display: inline-flex; align-items: center; justify-content: center; gap: 0.5rem; border: none; border-radius: var(--radius-s); padding: 0.7rem 1.2rem; font-weight: 600; font-size: 0.9rem; cursor: pointer; transition: transform 0.08s ease, filter 0.15s ease; }
  .btn:active { transform: translateY(1px); }
  .btn-primary { background: var(--brass); color: #201505; width: 100%; }
  .btn-primary:hover { filter: brightness(1.08); }
  .btn-primary:disabled { opacity: 0.6; cursor: not-allowed; }
  .form-msg { font-size: 0.85rem; padding: 0.6rem 0.8rem; border-radius: var(--radius-s); margin-bottom: 1rem; }
  .form-msg.error { background: rgba(193,87,63,0.15); color: var(--clay-strong); }
  .form-msg.success { background: rgba(79,163,150,0.15); color: var(--teal-strong); }
  @media (max-width: 860px) { .ledger { grid-template-columns: 1fr; } .ledger-cover { display: none; } }
</style>
</head>
<body>

<div id="auth-screen">
  <div class="ledger">
    <div class="ledger-cover">
      <div class="brand"><span class="mark">Almanac</span></div>
      <div>
        <h1>The book stays open for everyone.</h1>
        <p>One shared calendar for clients, providers, and admins — no double-bookings, no back-and-forth.</p>
        <div class="roles">
          <div><span class="tag" style="background:var(--muted)"></span>Client — books open slots</div>
          <div><span class="tag" style="background:var(--teal)"></span>Provider — sets weekly hours</div>
          <div><span class="tag" style="background:var(--brass)"></span>Admin — oversees the whole book</div>
        </div>
      </div>
      <span class="brand sub">v1 · secure · self-hosted</span>
    </div>
    <div class="ledger-form">
      <div class="tabs">
        <button class="tab active" data-mode="login" type="button">Sign in</button>
        <button class="tab" data-mode="register" type="button">Create account</button>
      </div>
      <div id="auth-msg" class="form-msg error hidden"></div>
      <form id="auth-form">
        <div class="field hidden" id="field-name">
          <label for="input-name">Full name</label>
          <input type="text" id="input-name" autocomplete="name">
        </div>
        <div class="field">
          <label for="input-email">Email</label>
          <input type="email" id="input-email" autocomplete="email" required>
        </div>
        <div class="field">
          <label for="input-password">Password</label>
          <input type="password" id="input-password" autocomplete="current-password" required minlength="8">
        </div>
        <div class="field hidden" id="field-role">
          <label>I am a</label>
          <div class="role-picker">
            <label><input type="radio" name="role" value="client" checked><span>Client</span></label>
            <label><input type="radio" name="role" value="provider"><span>Provider</span></label>
          </div>
        </div>
        <div class="field hidden" id="field-specialty">
          <label for="input-specialty">Specialty / service</label>
          <input type="text" id="input-specialty" placeholder="e.g. Dermatology, Haircuts, Tutoring">
        </div>
        <button type="submit" class="btn btn-primary" id="auth-submit">Sign in</button>
      </form>
    </div>
  </div>
</div>

<script>
  const API_BASE = "";
  const REDIRECT_ON_SUCCESS = "/";
  const TOKEN_KEY = "appt_token";
  const USER_KEY = "appt_user";

  function $(sel) { return document.querySelector(sel); }
  function $all(sel) { return [...document.querySelectorAll(sel)]; }

  async function apiPost(path, body) {
    const res = await fetch(API_BASE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (e) {}
    if (!res.ok) { throw new Error((data && data.error) || ("Request failed (" + res.status + ")")); }
    return data;
  }

  let mode = "login";
  const msgBox = $("#auth-msg");

  function getSelectedRole() {
    const checked = $all('input[name="role"]').find((r) => r.checked);
    return checked ? checked.value : "client";
  }

  function setMode(next) {
    mode = next;
    $all(".tab").forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
    $("#field-name").classList.toggle("hidden", mode !== "register");
    $("#field-role").classList.toggle("hidden", mode !== "register");
    $("#field-specialty").classList.toggle("hidden", !(mode === "register" && getSelectedRole() === "provider"));
    $("#auth-submit").textContent = mode === "login" ? "Sign in" : "Create account";
    msgBox.classList.add("hidden");
  }

  $all(".tab").forEach((t) => t.addEventListener("click", () => setMode(t.dataset.mode)));
  $all('input[name="role"]').forEach((r) =>
    r.addEventListener("change", () =>
      $("#field-specialty").classList.toggle("hidden", !(mode === "register" && getSelectedRole() === "provider"))
    )
  );

  $("#auth-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    msgBox.classList.add("hidden");
    const submitBtn = $("#auth-submit");
    submitBtn.disabled = true;

    const name = $("#input-name").value.trim();
    const email = $("#input-email").value.trim();
    const password = $("#input-password").value;

    try {
      let data;
      if (mode === "login") {
        data = await apiPost("/api/auth/login", { email: email, password: password });
      } else {
        data = await apiPost("/api/auth/register", {
          name: name, email: email, password: password,
          role: getSelectedRole(),
          specialty: $("#input-specialty").value.trim(),
        });
      }
      localStorage.setItem(TOKEN_KEY, data.token);
      localStorage.setItem(USER_KEY, JSON.stringify(data.user));

      msgBox.textContent = mode === "login" ? "Signed in — redirecting…" : "Account created — redirecting…";
      msgBox.classList.remove("error");
      msgBox.classList.add("success");
      msgBox.classList.remove("hidden");

      setTimeout(function () { window.location.href = REDIRECT_ON_SUCCESS; }, 500);
    } catch (err) {
      msgBox.textContent = err.message;
      msgBox.classList.remove("success");
      msgBox.classList.add("error");
      msgBox.classList.remove("hidden");
      submitBtn.disabled = false;
    }
  });

  setMode("login");
</script>
</body>
</html>
"""


@app.get("/")
def index():
    return Response(PAGE_HTML, mimetype="text/html")


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    print(f"Login page running at http://localhost:{port}")
    print(f"User database: {DB_PATH}")
    app.run(host="0.0.0.0", port=port, debug=debug)
