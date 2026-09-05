"""
routes/__init__.py
-------------------
Shared helpers for the HTTP layer.

One language on the wire
------------------------
The API used to speak two, decided by which handler you happened to hit.
Guest and catalogue endpoints returned camelCase from hand-built dicts;
appointments, availability, users and the email log returned `dict(row)`
straight out of SQLite, so the same client received `guestEmail` from one
endpoint and `start_time` from the next.

`coffee_chats.host_view` and `offerings.owner_view` fixed their half by
hand-writing a serialiser per shape. That does not scale to rows with a
dozen columns whose only transformation is the naming, so the rest goes
through `camel_keys`, which converts at the boundary and leaves snake_case
where it belongs: in the database, and in the SQL that talks to it.
"""


def camel(name):
    """`start_time` -> `startTime`. Anything already camel is left alone."""
    head, *rest = name.split("_")
    return head + "".join(word[:1].upper() + word[1:] for word in rest)


def camel_keys(value):
    """Recursively rename dict keys for the wire.

    Only keys. Values are untouched -- a `status` of "confirmed" is data, not
    a name, and rewriting data because it happens to look like an identifier
    is how you get a bug that only appears for the one user whose surname is
    `first_name`.
    """
    if isinstance(value, dict):
        return {camel(k): camel_keys(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [camel_keys(v) for v in value]
    return value
