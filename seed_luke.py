"""
seed_luke.py
-------------
Sets up Luke as a provider: a full week of availability and a priced
catalogue of software engineering, computer science and web development
sessions.

    python seed_luke.py                 # create or top up
    python seed_luke.py --reset         # wipe his offerings first
    python seed_luke.py --list          # show what is there

Idempotent by design. Running it twice does not create two accounts or
duplicate the catalogue, because the realistic use is running it again after
editing a description.

The prices are placeholders that read as plausible rather than researched --
free intro calls, student rates well under market, senior-level sessions
higher. Edit them in CATALOGUE; nothing else depends on the numbers.
"""

import argparse
import sys

import database as db
import offerings
from auth import hash_password

EMAIL = "luke@almanac.local"
PASSWORD = "coffee12345"
NAME = "Luke Zhang"
SPECIALTY = "Software engineering, CS fundamentals, web development"

# Sunday=0 .. Saturday=6, matching the availability table's convention.
# Weekdays run a full working day; the weekend is deliberately shorter and
# later, because a coffee chat on a Saturday morning at nine is a thing
# people book and then regret.
# The slot size is 15 minutes rather than 30 on purpose. A booking has to
# start on a slot boundary and end on one, so the grid size decides which
# session lengths are bookable at all: on a 30-minute grid a 45-minute
# session has *no* valid start time anywhere in the day. Fifteen divides
# every duration in the catalogue.
AVAILABILITY = [
    (0, "12:00", "16:00", 15),   # Sunday
    (1, "09:00", "17:00", 15),   # Monday
    (2, "09:00", "17:00", 15),   # Tuesday
    (3, "09:00", "17:00", 15),   # Wednesday
    (4, "09:00", "17:00", 15),   # Thursday
    (5, "09:00", "16:00", 15),   # Friday
    (6, "10:00", "15:00", 15),   # Saturday
]

# (title, category, level, minutes, price in cents, summary, description)
CATALOGUE = [
    # ---------------------------------------------------------- Intro
    ("Intro coffee chat", "Careers", "Intro", 30, 0,
     "A no-agenda first conversation. Free, always.",
     "Fifteen minutes of who you are and what you are aiming at, fifteen of "
     "whatever you want to ask. No preparation, no CV, nothing to send "
     "beforehand. If it turns out one of the paid sessions below would help, "
     "we can talk about that at the end -- and if it would not, I will say so."),

    # ------------------------------------------- Software engineering
    ("Technical mock interview", "Software engineering", "Any", 60, 9000,
     "A real interview, run properly, then honest feedback.",
     "One data structures and algorithms problem run the way an actual "
     "onsite runs: you drive, I probe, and I do not rescue you early. The "
     "last fifteen minutes are the part that matters -- what your "
     "communication looked like from the other side of the table, where you "
     "lost the interviewer, and what specifically to drill before the real "
     "one. Bring a language you are fluent in, not the one you think looks "
     "impressive."),

    ("System design interview", "Software engineering", "Mid", 60, 12000,
     "Design round practice for mid and senior loops.",
     "An open-ended design prompt with real constraints and follow-ups that "
     "get harder as you go. We cover how you scope an ambiguous problem, "
     "where you spend your minutes, and how to argue a trade-off instead of "
     "reciting one. Most people fail this round by designing in silence, and "
     "that is the habit this session is aimed at."),

    ("Code review on your own project", "Software engineering", "Any", 45, 6500,
     "Bring real code. I read it properly beforehand.",
     "Send a repository or a pull request twenty-four hours ahead and I will "
     "have actually read it. We go through structure, naming, error handling "
     "and the tests, and I tell you which of my comments are taste and which "
     "are defects, because conflating those is how review advice becomes "
     "noise."),

    ("Résumé and portfolio review", "Careers", "New grad", 30, 4000,
     "Line-by-line, aimed at engineering recruiters.",
     "Your résumé read the way somebody scanning two hundred of them reads "
     "it: six seconds, top third, does anything survive. We rewrite the "
     "bullets that describe duties into ones that describe outcomes, and cut "
     "whatever is padding. Send the PDF in advance."),

    ("New grad job search strategy", "Careers", "New grad", 45, 5000,
     "Where to apply, in what order, and when.",
     "Application timing, how referrals actually work, which postings are "
     "real and which are pipeline-filling, and how to keep several processes "
     "moving without losing track. For people who are sending applications "
     "into a void and want to know whether the void is the problem."),

    # -------------------------------------------- Computer science
    ("CS fundamentals tutoring", "Computer science", "Student", 60, 5500,
     "Data structures, algorithms, complexity -- taught, not drilled.",
     "For coursework or for interviews, but taught as understanding rather "
     "than pattern-matching. We work from where you actually are, which "
     "usually turns out to be one layer below where the confusion shows up. "
     "Recursion, trees, graphs, dynamic programming, and why big-O is a "
     "statement about growth and not about speed."),

    ("Algorithms problem clinic", "Computer science", "Student", 45, 4500,
     "Bring the problems that beat you.",
     "You bring two or three problems you could not finish. We do them "
     "together, slowly, and I show you the reasoning that gets from the "
     "statement to the approach -- which is the part editorials skip and the "
     "part that transfers to the next problem."),

    ("Discrete maths and theory help", "Computer science", "Student", 60, 5000,
     "Proofs, logic, automata, complexity classes.",
     "The theory courses that feel disconnected from programming until "
     "suddenly they are not. Induction, combinatorics, regular languages, "
     "reductions, P versus NP as an idea rather than a slogan. Bring your "
     "problem set."),

    # ------------------------------------------- Web development
    ("Full-stack project walkthrough", "Web development", "Junior", 60, 7000,
     "Take an app from idea to something that actually ships.",
     "Architecture for a real web application: what belongs on the server, "
     "what belongs in the browser, where state lives, and how the database "
     "shape decides most of the rest. We look at your project specifically "
     "rather than a generic to-do app, and I am blunt about which parts are "
     "over-engineered."),

    ("Frontend and UI code review", "Web development", "Junior", 45, 6000,
     "Components, state, accessibility, and why it feels slow.",
     "Your frontend read closely: component boundaries, state management "
     "that has grown past what it can carry, accessibility gaps that are "
     "cheap now and expensive later, and the handful of things actually "
     "responsible for the page feeling sluggish."),

    ("APIs and backend design", "Web development", "Mid", 60, 7500,
     "Endpoints, auth, and a schema you will not regret.",
     "REST design that survives a second consumer, authentication and "
     "authorisation done in the right layer, and database schemas built for "
     "the queries you will actually run. Bring an API you are designing or "
     "one you have inherited and cannot stand."),

    ("Deploy and ship your side project", "Web development", "Any", 45, 5500,
     "Get it off localhost and in front of people.",
     "Hosting choices and what they really cost, environment configuration, "
     "managed databases, domains and certificates, and enough logging to "
     "diagnose the first thing that breaks at three in the morning. By the "
     "end it is deployed, not planned."),

    ("Long-form mentorship session", "Careers", "Any", 90, 15000,
     "Ninety minutes on whatever the real problem is.",
     "For when the question is bigger than one topic: a career change, an "
     "offer decision, whether to leave, what to specialise in. Unstructured "
     "on purpose, because the useful part is usually not the thing you "
     "booked it to discuss."),
]


def ensure_luke(conn):
    user = db.query("SELECT * FROM users WHERE email = ?", (EMAIL,), one=True, conn=conn)
    if user:
        return user, False
    db.execute(
        """INSERT INTO users (name, email, password_hash, role, specialty)
           VALUES (?, ?, ?, 'provider', ?)""",
        (NAME, EMAIL, hash_password(PASSWORD), SPECIALTY), conn=conn)
    return db.query("SELECT * FROM users WHERE email = ?", (EMAIL,), one=True, conn=conn), True


def ensure_availability(provider_id, conn):
    """Add any missing weekly window. Existing ones are left alone so a hand
    edit is not overwritten by a re-run."""
    added = 0
    for day, start, end, slot in AVAILABILITY:
        existing = db.query(
            """SELECT id, slot_minutes FROM availability
               WHERE provider_id = ? AND day_of_week = ? AND start_time = ?""",
            (provider_id, day, start), one=True, conn=conn)
        if existing:
            # Correct a stale grid size in place. Leaving a 30-minute grid
            # behind would silently make the 45-minute sessions unbookable.
            if existing["slot_minutes"] != slot:
                db.execute("UPDATE availability SET slot_minutes = ? WHERE id = ?",
                           (slot, existing["id"]), conn=conn)
            continue
        db.execute(
            """INSERT INTO availability
               (provider_id, day_of_week, start_time, end_time, slot_minutes)
               VALUES (?, ?, ?, ?, ?)""",
            (provider_id, day, start, end, slot), conn=conn)
        added += 1
    return added


def ensure_offerings(provider_id, conn, reset=False):
    if reset:
        db.execute("DELETE FROM offerings WHERE provider_id = ?", (provider_id,), conn=conn)

    added = 0
    for order, (title, cat, level, mins, cents, summary, desc) in enumerate(CATALOGUE):
        existing = db.query(
            "SELECT id FROM offerings WHERE provider_id = ? AND title = ?",
            (provider_id, title), one=True, conn=conn)
        if existing:
            continue
        offerings.create(provider_id, title=title, category=cat, level=level,
                         duration_min=mins, price_cents=cents, currency="CAD",
                         summary=summary, description=desc, sort_order=order,
                         conn=conn)
        added += 1
    return added


def show(provider_id, conn=None):
    """Print the catalogue. Takes the open connection, because standalone
    scripts have no Flask application context for db.get_db() to find."""
    total = 0
    for group in _grouped(provider_id, conn):
        print(f"\n  {group['category']}")
        print("  " + "-" * (len(group["category"]) + 2))
        for o in group["offerings"]:
            total += 1
            level = f"[{o['level']}]" if o["level"] else ""
            print(f"    {o['title']:<36} {o['durationMin']:>3} min "
                  f"{o['price']:>10}  {level}")
            if o["summary"]:
                print(f"      {o['summary']}")
    print(f"\n  {total} offering(s)")


def _grouped(provider_id, conn):
    rows = offerings.list_for_provider(provider_id, conn=conn)
    buckets = {}
    for row in rows:
        buckets.setdefault(row["category"] or "Other", []).append(
            offerings.public_view(row))
    ordered = [{"category": c, "offerings": buckets.pop(c)}
               for c in offerings.CATEGORIES if c in buckets]
    return ordered + [{"category": c, "offerings": v} for c, v in buckets.items()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="delete existing offerings before seeding")
    ap.add_argument("--list", action="store_true", help="show and exit")
    args = ap.parse_args()

    with db.standalone_connection() as conn:
        db.init_db(conn=conn)
        user, created = ensure_luke(conn)
        provider_id = user["id"]

        if args.list:
            show(provider_id, conn)
            return 0

        windows = ensure_availability(provider_id, conn)
        added = ensure_offerings(provider_id, conn, reset=args.reset)

        print(f"{'Created' if created else 'Found'} provider {NAME} <{EMAIL}>")
        if created:
            print(f"  password: {PASSWORD}")
        print(f"  availability windows added: {windows}")
        print(f"  offerings added: {added}")
        show(provider_id, conn)
    print(f"\nGuests can browse these at /api/providers/{provider_id}/offerings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
