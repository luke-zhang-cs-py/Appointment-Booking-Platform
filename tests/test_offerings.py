"""offerings.py holds prices, so its arithmetic and its validation are the
two things worth pinning down."""

import pytest


# ------------------------------------------------------------------- money

@pytest.mark.parametrize("cents,expected", [
    (0, "Free"),
    (9000, "$90 CAD"),
    (9050, "$90.50 CAD"),
    (100, "$1 CAD"),
    (5, "$0.05 CAD"),
])
def test_money_formatting(cents, expected):
    from offerings import money
    assert money(cents, "CAD") == expected


def test_money_free_beats_every_other_rule():
    """Zero is a real price meaning free, not a missing one."""
    from offerings import money
    assert money(0, "GBP") == "Free"
    assert money(None, "CAD") == "Free"


def test_money_symbols():
    from offerings import money
    assert money(1000, "GBP") == "£10"
    assert money(1000, "EUR") == "€10"
    assert money(1000, "JPY") == "10", "unknown currency degrades to bare digits"


# -------------------------------------------------------------- validation

def test_title_is_required(ctx, provider):
    from offerings import create, OfferingError
    with pytest.raises(OfferingError):
        create(provider["id"], title="   ")


def test_duration_must_tile_a_grid(ctx, provider):
    from offerings import create, OfferingError, SLOT_MULTIPLES
    with pytest.raises(OfferingError):
        create(provider["id"], title="Odd", duration_min=37)
    for d in SLOT_MULTIPLES:
        assert create(provider["id"], title=f"Fine {d}", duration_min=d)


def test_price_cannot_be_negative(ctx, provider):
    from offerings import create, OfferingError
    with pytest.raises(OfferingError):
        create(provider["id"], title="Owed money", price_cents=-1)


def test_zero_price_is_allowed(ctx, provider):
    from offerings import create, get
    oid = create(provider["id"], title="Free intro", price_cents=0)
    assert get(oid)["price_cents"] == 0


# ----------------------------------------------------------------- listing

def test_list_hides_deactivated_by_default(ctx, provider):
    from offerings import create, deactivate, list_for_provider
    oid = create(provider["id"], title="Temporary")
    assert any(o["id"] == oid for o in list_for_provider(provider["id"]))
    deactivate(oid, provider["id"])
    assert not any(o["id"] == oid for o in list_for_provider(provider["id"]))
    assert any(o["id"] == oid
               for o in list_for_provider(provider["id"], active_only=False))


def test_deactivate_never_deletes(ctx, provider):
    """Bookings reference the row; 'the session you paid for no longer
    exists' is worse than showing what it was."""
    from offerings import create, deactivate, get
    oid = create(provider["id"], title="Retired")
    deactivate(oid, provider["id"])
    assert get(oid) is not None
    assert get(oid)["is_active"] == 0


def test_cannot_touch_someone_elses_offering(ctx, provider, client):
    from offerings import create, update, deactivate, OfferingError
    from tests.conftest import register
    _, other = register(client, "other@test.local", role="provider")
    oid = create(provider["id"], title="Mine")
    with pytest.raises(OfferingError):
        update(oid, other["id"], title="Stolen")
    with pytest.raises(OfferingError):
        deactivate(oid, other["id"])


def test_update_validates_the_merged_row(ctx, provider):
    """Patching only the duration must still be checked against the rest."""
    from offerings import create, update, OfferingError
    oid = create(provider["id"], title="Session", duration_min=30)
    with pytest.raises(OfferingError):
        update(oid, provider["id"], duration_min=37)
    assert update(oid, provider["id"], duration_min=60)["duration_min"] == 60


def test_update_with_nothing_to_change_is_a_no_op(ctx, provider):
    from offerings import create, update
    oid = create(provider["id"], title="Unchanged")
    assert update(oid, provider["id"])["title"] == "Unchanged"


def test_grouping_follows_the_category_order(ctx, provider):
    from offerings import create, grouped_for_provider, CATEGORIES
    create(provider["id"], title="W", category="Web development")
    create(provider["id"], title="S", category="Software engineering")
    create(provider["id"], title="C", category="Computer science")
    order = [g["category"] for g in grouped_for_provider(provider["id"])]
    assert order == [c for c in CATEGORIES if c in order]


def test_unknown_category_still_appears(ctx, provider):
    """Ordering by a constant must not silently drop anything outside it."""
    from offerings import create, grouped_for_provider
    create(provider["id"], title="Odd one", category="Underwater basketry")
    cats = [g["category"] for g in grouped_for_provider(provider["id"])]
    assert "Underwater basketry" in cats


def test_public_view_formats_money_and_flags_free(ctx, provider):
    from offerings import create, get, public_view
    paid = public_view(get(create(provider["id"], title="Paid", price_cents=4500)))
    free = public_view(get(create(provider["id"], title="Free", price_cents=0)))
    assert paid["price"] == "$45 CAD" and paid["isFree"] is False
    assert free["price"] == "Free" and free["isFree"] is True
