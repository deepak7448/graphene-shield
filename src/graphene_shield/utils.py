from __future__ import annotations

import html
from typing import Any

from graphql_relay import from_global_id


def enum_value(val: Any) -> Any:
    """Return the underlying value of a Graphene/Python enum member, or ``val`` as-is."""
    if val is None:
        return None
    return getattr(val, "value", val)


def clean_html_entities(text: str) -> str:
    """Repeatedly unescape HTML entities (``&amp;amp;`` → ``&``) until stable."""
    previous = None
    current = text
    while previous != current:
        previous = current
        current = html.unescape(current)
    return current


# Values under keys containing any of these are never altered (passwords may
# legitimately start or end with spaces or contain "&amp;").
SENSITIVE_KEY_PARTS = ("password", "secret", "token")


def _is_sensitive(key: Any) -> bool:
    return isinstance(key, str) and any(part in key.lower() for part in SENSITIVE_KEY_PARTS)


def sanitize_input(value: Any, *, unescape_html: bool = False) -> Any:
    """Recursively strip strings inside dicts/lists, optionally HTML-unescaping them.

    Container types are preserved, so Graphene ``InputObjectType`` values keep
    attribute access (``data.input.name``). Values under sensitive keys
    (``SENSITIVE_KEY_PARTS``) are left exactly as sent.

    ``unescape_html`` turns ``&lt;script&gt;`` into ``<script>``. It is off by
    default because it defeats escaping done upstream and changes data that
    really contains entities.
    """
    if isinstance(value, str):
        value = value.strip()
        return clean_html_entities(value) if unescape_html else value
    if isinstance(value, dict):
        cleaned = {
            k: v if _is_sensitive(k) else sanitize_input(v, unescape_html=unescape_html)
            for k, v in value.items()
        }
        if type(value) is dict:
            return cleaned
        try:
            return type(value)(cleaned)
        except Exception:
            return cleaned
    if isinstance(value, list):
        return [sanitize_input(item, unescape_html=unescape_html) for item in value]
    return value


def is_effectively_empty(value: Any) -> bool:
    """True for None, blank strings, and lists/dicts whose items are all effectively empty.

    >>> is_effectively_empty([None, "  ", {"a": ""}])
    True
    >>> is_effectively_empty(0)
    False
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set)):
        return all(is_effectively_empty(v) for v in value)
    if isinstance(value, dict):
        return all(is_effectively_empty(v) for v in value.values())
    return False


def resolve_global_id(global_id: str, expected_type: str | None = None) -> str:
    """Decode a Relay global ID and return the database id.

    Raises ``ValueError`` for undecodable ids or when ``expected_type`` doesn't match.
    """
    try:
        type_name, pk = from_global_id(global_id)
    except Exception as exc:  # graphql_relay raises assorted decode errors
        raise ValueError(f"Invalid ID: {global_id!r}") from exc
    if not pk or (expected_type and type_name != expected_type):
        raise ValueError(f"Invalid ID: {global_id!r}")
    return pk
