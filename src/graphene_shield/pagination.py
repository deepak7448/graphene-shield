"""Pagination strategies used by ``FilterConnectionField`` / ``ModelListField``.

Pick one with the ``PAGINATION`` setting, or per field with ``pagination=...``:

- ``"offset"``: Relay connection; cursors are row positions (``LIMIT/OFFSET``),
  plus an ``offset`` argument. Lets clients jump to any position.
- ``"keyset"``: Relay connection; cursors hold the last row's sort values
  (``WHERE (sort columns) > (cursor values)``). Stays fast on deep pages and
  never skips or repeats rows when data changes, but can't jump to a position.
- ``"page"``: ``page`` / ``pageSize`` arguments returning ``items`` and
  ``pageInfo { page pageSize totalCount totalPages hasNextPage hasPreviousPage }``.

In every mode the ``COUNT(*)`` query only runs when the client selects
``totalCount`` (or ``totalPages``).
"""

from __future__ import annotations

import base64
import binascii
import datetime
import decimal
import json
import math
import uuid
from typing import Any

import graphene
from django.core.exceptions import FieldError, ImproperlyConfigured, ValidationError
from django.db.models import F, Q
from django.db.models.query import QuerySet
from graphene.relay.connection import connection_adapter, page_info_adapter
from graphql_relay import get_offset_with_default, offset_to_cursor

from graphene_shield.messages import CommonErrorMessages
from graphene_shield.responses import EnhancedGraphQLError

PAGINATION_MODES = ("offset", "keyset", "page")


def bad_request(detail: str) -> EnhancedGraphQLError:
    return EnhancedGraphQLError(
        message=f"{CommonErrorMessages.BAD_REQUEST.message}: {detail}",
        message_code=CommonErrorMessages.BAD_REQUEST.code,
        status=False,
        data=[],
    )


def ensure_ordered(iterable):
    """Give an unordered queryset a stable order (by primary key) so pages don't overlap."""
    if isinstance(iterable, QuerySet) and not iterable.ordered:
        return iterable.order_by("pk")
    return iterable


def _count(iterable) -> int:
    return iterable.count() if isinstance(iterable, QuerySet) else len(iterable)


def _finish(connection, edges, has_previous, has_next, iterable, length=None):
    page_info = page_info_adapter(
        edges[0].cursor if edges else None,
        edges[-1].cursor if edges else None,
        has_previous,
        has_next,
    )
    result = connection_adapter(connection, edges, page_info)
    result.iterable = iterable  # totalCount counts this lazily
    result.length = length
    return result


# ── offset ──


def offset_forward_connection(connection, iterable, after, first):
    """``first``/``after`` page without a COUNT query (fetches one extra row instead)."""
    start = get_offset_with_default(after, -1) + 1
    rows = list(iterable[start : start + first + 1])
    has_next = len(rows) > first
    edges = [
        connection.Edge(node=node, cursor=offset_to_cursor(start + i))
        for i, node in enumerate(rows[:first])
    ]
    return _finish(connection, edges, start > 0, has_next, iterable)


# ── keyset ──


def get_ordering(queryset) -> list[tuple[str, bool]]:
    """The queryset's ordering as ``[(field, descending)]``, ending with the primary key."""
    query = queryset.query
    order = list(query.order_by) or (
        list(query.get_meta().ordering or []) if query.default_ordering else []
    )
    ordering = []
    for item in order:
        if not isinstance(item, str) or item == "?":
            raise ImproperlyConfigured(
                "Keyset pagination needs a queryset ordered by field names; got "
                f"{item!r}. Order by plain fields (e.g. order_by('-created_at'))."
            )
        ordering.append((item.lstrip("-+"), item.startswith("-")))
    pk_names = {"pk", queryset.model._meta.pk.name}
    if not any(name in pk_names for name, _ in ordering):
        ordering.append(("pk", ordering[-1][1] if ordering else False))
    return ordering


def _signature(ordering) -> str:
    return ",".join(("-" if desc else "") + name for name, desc in ordering)


def _cursor_value(value: Any) -> Any:
    # Full precision: DjangoJSONEncoder would cut datetimes to milliseconds, and
    # rows created within the same millisecond would then be skipped.
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    raise TypeError(f"Can't put {type(value).__name__} in a keyset cursor")


def encode_keyset_cursor(ordering, values: list[Any]) -> str:
    payload = json.dumps({"o": _signature(ordering), "v": values}, default=_cursor_value)
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_keyset_cursor(cursor: str, ordering) -> list[Any]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        values, signature = payload["v"], payload["o"]
    except (binascii.Error, ValueError, TypeError, KeyError, AttributeError):
        raise bad_request("the cursor is not valid.") from None
    if signature != _signature(ordering) or not isinstance(values, list):
        raise bad_request("the cursor belongs to a different sort order.")
    if len(values) != len(ordering):
        raise bad_request("the cursor is not valid.")
    return values


def _seek(ordering, values, forward: bool) -> Q:
    """Rows strictly after (forward) or before the row with ``values``.

    For ``a ASC, b DESC``: ``(a > x) OR (a = x AND b < y)``.
    """
    condition = Q()
    equal = Q()
    for (name, desc), value in zip(ordering, values, strict=True):
        if value is None:
            raise bad_request(
                f"keyset pagination can't continue past a row with an empty `{name}`."
            )
        lookup = "lt" if desc == forward else "gt"
        condition |= equal & Q(**{f"{name}__{lookup}": value})
        equal &= Q(**{name: value})
    return condition


def keyset_connection(connection, queryset, args, default_size):
    first, last = args.get("first"), args.get("last")
    if first is not None and last is not None:
        raise bad_request("use either `first` or `last` with keyset pagination, not both.")
    if not isinstance(queryset, QuerySet):
        raise ImproperlyConfigured("Keyset pagination needs the resolver to return a QuerySet.")

    ordering = get_ordering(queryset)
    keys = {f"_shield_key_{i}": F(name) for i, (name, _) in enumerate(ordering)}
    backward = last is not None
    size = last if backward else (first if first is not None else default_size)
    cursor = args.get("before") if backward else args.get("after")

    qs = queryset.annotate(**keys)
    if cursor:
        values = decode_keyset_cursor(cursor, ordering)
        try:
            qs = qs.filter(_seek(ordering, values, forward=not backward))
        except (ValueError, TypeError, ValidationError, FieldError):
            raise bad_request("the cursor is not valid.") from None
    # Walk backwards by flipping every direction, then restore the order.
    qs = qs.order_by(*(("-" if desc != backward else "") + name for name, desc in ordering))

    rows = list(qs[: size + 1]) if size is not None else list(qs)
    more = size is not None and len(rows) > size
    rows = rows[:size] if size is not None else rows
    if backward:
        rows.reverse()

    edges = [
        connection.Edge(
            node=row, cursor=encode_keyset_cursor(ordering, [getattr(row, k) for k in keys])
        )
        for row in rows
    ]
    if backward:
        has_previous, has_next = more, bool(cursor)
    else:
        has_previous, has_next = bool(cursor), more
    return _finish(connection, edges, has_previous, has_next, queryset)


# ── page numbers ──


class PageNumberInfo(graphene.ObjectType):
    page = graphene.Int(required=True, description="Current page, starting at 1.")
    page_size = graphene.Int(required=True, description="Maximum items per page.")
    total_count = graphene.Int(
        required=True, description="Total number of items. Runs a COUNT query when selected."
    )
    total_pages = graphene.Int(
        required=True, description="Total number of pages. Runs a COUNT query when selected."
    )
    has_next_page = graphene.Boolean(required=True)
    has_previous_page = graphene.Boolean(required=True)

    @staticmethod
    def resolve_total_count(root, info):
        return root.count()

    @staticmethod
    def resolve_total_pages(root, info):
        return math.ceil(root.count() / root.page_size)


class PageState:
    def __init__(self, iterable, page, page_size, has_next_page):
        self.iterable = iterable
        self.page = page
        self.page_size = page_size
        self.has_next_page = has_next_page
        self.has_previous_page = page > 1
        self._count = None

    def count(self) -> int:
        if self._count is None:
            self._count = _count(self.iterable)
        return self._count


class PageResult:
    def __init__(self, items, page_info):
        self.items = items
        self.page_info = page_info


_page_types: dict[type, type] = {}


def page_type_for(node_type) -> type:
    """``<Node>Page { items: [Node!]!, pageInfo: PageNumberInfo! }``, created once per node."""
    if node_type not in _page_types:
        _page_types[node_type] = type(
            f"{node_type._meta.name}Page",
            (graphene.ObjectType,),
            {
                "__doc__": f"A page of {node_type._meta.name} items.",
                "items": graphene.NonNull(graphene.List(graphene.NonNull(node_type))),
                "page_info": graphene.NonNull(PageNumberInfo),
            },
        )
    return _page_types[node_type]


def page_result(iterable, page: int, page_size: int) -> PageResult:
    start = (page - 1) * page_size
    rows = list(iterable[start : start + page_size + 1])
    has_next = len(rows) > page_size
    return PageResult(rows[:page_size], PageState(iterable, page, page_size, has_next))
