"""
routes/offering_routes.py
--------------------------
A provider's priced catalogue: managed by them, browsable by anyone.

The public list is deliberately unauthenticated. Prices and descriptions are
the thing you send people to look at, and putting a login in front of a
menu defeats the point -- the same reasoning that keeps the coffee chat
booking page open to a token holder.
"""

from flask import Blueprint, g, jsonify, request

import database as db
import offerings
from auth import roles_required, token_required
from offerings import OfferingError

bp = Blueprint("offering_routes", __name__, url_prefix="/api")


def _fail(exc, code=400):
    return jsonify({"error": str(exc)}), code


# ------------------------------------------------------------------ public

@bp.get("/providers/<int:provider_id>/offerings")
def public_offerings(provider_id):
    """Everything a provider currently offers, grouped for display."""
    provider = db.query(
        "SELECT id, name, specialty FROM users WHERE id = ? AND role = 'provider'",
        (provider_id,), one=True)
    if not provider:
        return _fail("Provider not found.", 404)

    groups = offerings.grouped_for_provider(provider_id)
    flat = [o for grp in groups for o in grp["offerings"]]

    return jsonify({
        "provider": {"id": provider["id"], "name": provider["name"],
                     "specialty": provider["specialty"]},
        "groups": groups,
        "count": len(flat),
        # A range is more use on a landing page than a list of every price,
        # and "from free" is the honest way to say it when an intro exists.
        "priceRange": _price_range(flat),
    })


def _price_range(items):
    """A summary line for a landing page.

    Pulled out of the handler because it was a conditional expression nested
    three deep -- the kind that is quicker to rewrite than to read.
    """
    if not items:
        return None
    paid = sorted((o for o in items if not o["isFree"]),
                  key=lambda o: o["priceCents"])
    has_free = any(o["isFree"] for o in items)

    if not paid:
        label = "Free"
    elif has_free:
        label = f"From free to {paid[-1]['price']}"
    elif paid[0]["price"] == paid[-1]["price"]:
        label = paid[0]["price"]
    else:
        label = f"{paid[0]['price']} – {paid[-1]['price']}"

    return {
        "hasFree": has_free,
        "minPaid": paid[0]["priceCents"] if paid else None,
        "maxPaid": paid[-1]["priceCents"] if paid else None,
        "label": label,
    }


# ------------------------------------------------------------------- owner

@bp.get("/offerings/mine")
@token_required
@roles_required("provider", "admin")
def my_offerings():
    rows = offerings.list_for_provider(g.current_user["id"], active_only=False)
    return jsonify({"offerings": [offerings.owner_view(r) for r in rows]})


@bp.post("/offerings/mine")
@token_required
@roles_required("provider", "admin")
def create_offering():
    try:
        draft = offerings.OfferingDraft.from_payload(request.get_json(silent=True))
        new_id = offerings.create(g.current_user["id"], draft)
    except OfferingError as exc:
        return _fail(exc)
    return jsonify({"offering": offerings.owner_view(offerings.get(new_id))}), 201


@bp.patch("/offerings/mine/<int:offering_id>")
@token_required
@roles_required("provider", "admin")
def update_offering(offering_id):
    body = request.get_json(silent=True) or {}
    fields = {
        "title": body.get("title"),
        "category": body.get("category"),
        "summary": body.get("summary"),
        "description": body.get("description"),
        "level": body.get("level"),
        "currency": body.get("currency"),
    }
    for src, dest in (("durationMin", "duration_min"), ("priceCents", "price_cents"),
                      ("sortOrder", "sort_order"), ("isActive", "is_active")):
        if body.get(src) is not None:
            try:
                fields[dest] = int(body[src])
            except (TypeError, ValueError):
                return _fail(f"{src} must be a whole number.")
    try:
        updated = offerings.update(offering_id, g.current_user["id"], **fields)
    except OfferingError as exc:
        return _fail(exc, 404 if "not found" in str(exc).lower() else 400)
    return jsonify({"offering": offerings.owner_view(updated)})


@bp.delete("/offerings/mine/<int:offering_id>")
@token_required
@roles_required("provider", "admin")
def remove_offering(offering_id):
    """Deactivates rather than deletes -- past bookings still reference it."""
    try:
        offering = offerings.deactivate(offering_id, g.current_user["id"])
    except OfferingError as exc:
        return _fail(exc, 404)
    return jsonify({"offering": offerings.owner_view(offering)})
