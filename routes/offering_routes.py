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
    free = [o for o in flat if o["isFree"]]
    paid = [o for o in flat if not o["isFree"]]

    return jsonify({
        "provider": {"id": provider["id"], "name": provider["name"],
                     "specialty": provider["specialty"]},
        "groups": groups,
        "count": len(flat),
        # A range is more use on a landing page than a list of every price,
        # and "from free" is the honest way to say it when an intro exists.
        "priceRange": None if not flat else {
            "hasFree": bool(free),
            "minPaid": min((o["priceCents"] for o in paid), default=None),
            "maxPaid": max((o["priceCents"] for o in paid), default=None),
            "label": (
                "Free" if not paid else
                (f"From free to {max(paid, key=lambda o: o['priceCents'])['price']}"
                 if free else
                 f"{min(paid, key=lambda o: o['priceCents'])['price']}"
                 f" – {max(paid, key=lambda o: o['priceCents'])['price']}")
            ),
        },
    })


# ------------------------------------------------------------------- owner

@bp.get("/offerings/mine")
@token_required
@roles_required("provider", "admin")
def my_offerings():
    rows = offerings.list_for_provider(g.current_user["id"], active_only=False)
    return jsonify({"offerings": [dict(r) for r in rows]})


@bp.post("/offerings/mine")
@token_required
@roles_required("provider", "admin")
def create_offering():
    body = request.get_json(silent=True) or {}
    try:
        new_id = offerings.create(
            provider_id=g.current_user["id"],
            title=body.get("title"),
            category=body.get("category"),
            summary=body.get("summary"),
            description=body.get("description"),
            level=body.get("level"),
            duration_min=int(body.get("durationMin") or 30),
            price_cents=int(body.get("priceCents") or 0),
            currency=body.get("currency") or "CAD",
            sort_order=int(body.get("sortOrder") or 0),
        )
    except OfferingError as exc:
        return _fail(exc)
    except (TypeError, ValueError):
        return _fail("Duration and price must be whole numbers.")
    return jsonify({"offering": dict(offerings.get(new_id))}), 201


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
    return jsonify({"offering": dict(updated)})


@bp.delete("/offerings/mine/<int:offering_id>")
@token_required
@roles_required("provider", "admin")
def remove_offering(offering_id):
    """Deactivates rather than deletes -- past bookings still reference it."""
    try:
        offering = offerings.deactivate(offering_id, g.current_user["id"])
    except OfferingError as exc:
        return _fail(exc, 404)
    return jsonify({"offering": dict(offering)})
