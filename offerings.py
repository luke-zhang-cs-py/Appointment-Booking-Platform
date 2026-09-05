"""
offerings.py
-------------
What a provider offers, and what it costs.

A provider used to have one free-text `specialty` on their user row, which
cannot express "I run mock interviews at one rate, review portfolios at
another, and do a free intro chat". This is that list: several priced things
per provider, each with its own length and description, any of which a guest
can book.

Money is stored in minor units as an integer -- cents for CAD/USD, pence for
GBP. Floats and money is a bug waiting for a decimal to land wrong, and the
platform will eventually total these. Zero is a real value meaning free, not
"unset": a free intro call is a deliberate offering, not a missing price, so
`price_cents` is NOT NULL with a default of 0 and nothing here treats 0 as
absent.

Durations must line up with how availability is generated. calendar_logic
slices a provider's window into fixed-size slots, so an offering that runs
longer than one slot occupies several consecutive ones; SLOT_MULTIPLES is the
set of lengths that divide cleanly into the usual 15- and 30-minute grids.
"""

import logging
from dataclasses import dataclass, fields

import database as db

log = logging.getLogger(__name__)

# A booking must start and end on a slot boundary, so the provider's grid
# size decides which of these are bookable at all. On a 30-minute grid a
# 45-minute session has no valid start time anywhere in the day -- it is not
# rare, it is impossible. A 15-minute grid supports every value here, which
# is why seed_luke sets one.
SLOT_MULTIPLES = (15, 30, 45, 60, 90)

# Half an hour is the length somebody means when they do not say a length.
DEFAULT_DURATION_MIN = 30
DEFAULT_CURRENCY = "CAD"

# Kept deliberately small. Categories are for grouping a booking page into
# readable sections, and a list of twenty is not a grouping.
CATEGORIES = ("Software engineering", "Computer science", "Web development",
              "Careers", "Other")

LEVELS = ("Intro", "Student", "New grad", "Junior", "Mid", "Senior", "Any")


class OfferingError(Exception):
    """Something the caller can fix: bad price, bad duration, unknown id."""


def money(price_cents, currency="CAD"):
    """Render a price the way a person reads it.

    Free is worth saying in words. A price that happens to be a round number
    of dollars does not need the trailing zeros, because "$80" reads faster
    than "$80.00" and this appears in a list somebody is scanning.
    """
    if not price_cents:
        return "Free"
    symbol = {"CAD": "$", "USD": "$", "GBP": "£", "EUR": "€"}.get(currency, "")
    whole, cents = divmod(int(price_cents), 100)
    body = f"{whole}" if cents == 0 else f"{whole}.{cents:02d}"
    suffix = f" {currency}" if currency in ("CAD", "USD") else ""
    return f"{symbol}{body}{suffix}"


def _validate(title, duration_min, price_cents):
    if not (title or "").strip():
        raise OfferingError("A title is required.")
    if duration_min not in SLOT_MULTIPLES:
        raise OfferingError(
            f"Duration must be one of {', '.join(str(d) for d in SLOT_MULTIPLES)} minutes.")
    if price_cents is None or int(price_cents) < 0:
        raise OfferingError("Price cannot be negative. Use 0 for a free session.")


@dataclass
class OfferingDraft:
    """One thing a provider is putting on their list, before it has an id.

    create() used to take eleven parameters. Eleven arguments in a row is a
    thing pretending to be a signature: they always travel together, they are
    meaningless apart, and every call site was already writing them out in
    the same order and hoping. Naming the thing means the caller describes an
    offering instead of remembering an argument order, and a new field is one
    line here rather than a change to every signature it passes through.

    from_payload() is where the API shape (camelCase, strings out of JSON)
    stops and the domain shape starts, so nothing downstream has to know a
    request was involved.
    """

    title: str
    duration_min: int = DEFAULT_DURATION_MIN
    price_cents: int = 0
    currency: str = DEFAULT_CURRENCY
    category: str = None
    summary: str = None
    description: str = None
    level: str = None
    sort_order: int = 0

    @classmethod
    def from_payload(cls, body):
        """Build a draft from a JSON request body.

        Raises OfferingError rather than letting a ValueError out, so a route
        has one kind of failure to catch instead of two.
        """
        body = body or {}
        try:
            return cls(
                title=body.get("title"),
                duration_min=int(body.get("durationMin") or DEFAULT_DURATION_MIN),
                price_cents=int(body.get("priceCents") or 0),
                currency=body.get("currency") or DEFAULT_CURRENCY,
                category=body.get("category"),
                summary=body.get("summary"),
                description=body.get("description"),
                level=body.get("level"),
                sort_order=int(body.get("sortOrder") or 0),
            )
        except (TypeError, ValueError):
            raise OfferingError("Duration, price and sort order must be whole numbers.")

    def validate(self):
        """The same rules update() applies to a patched row. One place."""
        _validate(self.title, self.duration_min, self.price_cents)


def create(provider_id, draft, conn=None):
    """Add a draft to a provider's catalogue. Returns the new id."""
    draft.validate()
    return db.insert(
        """INSERT INTO offerings
           (provider_id, title, category, summary, description, duration_min,
            price_cents, currency, level, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (provider_id, draft.title.strip(), draft.category, draft.summary,
         draft.description, int(draft.duration_min), int(draft.price_cents),
         draft.currency, draft.level, int(draft.sort_order)),
        conn=conn)


def get(offering_id, conn=None):
    return db.query("SELECT * FROM offerings WHERE id = ?", (offering_id,),
                    one=True, conn=conn)


def list_for_provider(provider_id, active_only=True, conn=None):
    sql = "SELECT * FROM offerings WHERE provider_id = ?"
    params = [provider_id]
    if active_only:
        sql += " AND is_active = 1"
    sql += " ORDER BY sort_order, id"
    return db.query(sql, tuple(params), conn=conn)


def update(offering_id, owner_id, **patch):
    """Patch an offering. Only the caller's own rows, only known columns.

    `owner_id` rather than `provider_id` because the patch arrives as
    keyword arguments: a caller passing provider_id="..." collided with this
    parameter and raised TypeError rather than being rejected as an unknown
    column. The route never sends one, so it was a trap for direct callers
    rather than a live fault -- but it failed with a 500 instead of a 400.
    """
    existing = get(offering_id)
    if not existing or existing["provider_id"] != owner_id:
        raise OfferingError("Offering not found.")

    # Patchable columns are the draft's fields plus is_active, which is not
    # something you set when creating one. Deriving it from OfferingDraft
    # rather than repeating the list means adding a field cannot leave a
    # column creatable but not editable.
    allowed = tuple(f.name for f in fields(OfferingDraft)) + ("is_active",)
    changes = {k: v for k, v in patch.items() if k in allowed and v is not None}
    if not changes:
        return existing

    _validate(changes.get("title", existing["title"]),
              int(changes.get("duration_min", existing["duration_min"])),
              int(changes.get("price_cents", existing["price_cents"])))

    sets = ", ".join(f"{k} = ?" for k in changes)
    db.execute(f"UPDATE offerings SET {sets} WHERE id = ?",
               tuple(changes.values()) + (offering_id,))
    return get(offering_id)


def deactivate(offering_id, owner_id):
    """Hide an offering rather than delete it.

    Appointments and invites reference it, and a booking that says "the
    session you paid for no longer exists" is worse than one that says what
    it was. is_active keeps history readable.
    """
    existing = get(offering_id)
    if not existing or existing["provider_id"] != owner_id:
        raise OfferingError("Offering not found.")
    db.execute("UPDATE offerings SET is_active = 0 WHERE id = ?", (offering_id,))
    return get(offering_id)


def public_view(offering):
    """The shape a booking page wants: money already formatted."""
    return {
        "id": offering["id"],
        "title": offering["title"],
        "category": offering["category"],
        "summary": offering["summary"],
        "description": offering["description"],
        "durationMin": offering["duration_min"],
        "level": offering["level"],
        "priceCents": offering["price_cents"],
        "price": money(offering["price_cents"], offering["currency"]),
        "currency": offering["currency"],
        "isFree": not offering["price_cents"],
    }


def owner_view(offering):
    """An offering as its owner sees it: the public shape plus the fields
    only they need. Same casing as everything else on the wire."""
    view = public_view(offering)
    view["isActive"] = bool(offering["is_active"])
    view["sortOrder"] = offering["sort_order"]
    return view


def grouped_for_provider(provider_id):
    """Offerings bucketed by category, in CATEGORIES order.

    Ordering by the constant rather than alphabetically keeps the sections
    in the order a reader expects them, and anything with an unrecognised
    category still appears instead of being silently dropped.
    """
    rows = list_for_provider(provider_id)
    buckets = {}
    for row in rows:
        buckets.setdefault(row["category"] or "Other", []).append(public_view(row))

    ordered = [{"category": c, "offerings": buckets.pop(c)}
               for c in CATEGORIES if c in buckets]
    ordered += [{"category": c, "offerings": v} for c, v in buckets.items()]
    return ordered
