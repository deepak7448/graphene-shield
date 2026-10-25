"""Sorting, searching and filtering for list queries."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import graphene
from django.core.exceptions import FieldError
from django.db.models import Q

from graphene_shield.connections import FilterConnectionField
from graphene_shield.messages import CommonErrorMessages
from graphene_shield.responses import EnhancedGraphQLError
from graphene_shield.sorting import SortingInput

logger = logging.getLogger("graphene_shield.queries")


def _direction_is_desc(direction: Any) -> bool:
    name = getattr(direction, "name", direction)
    if name in ("DESC", "-"):
        return True
    if name in ("ASC", "", None):
        return False
    raise ValueError(f"Invalid sorting direction: {direction!r}")


def _sort_fields(field: Any) -> list[str]:
    value = getattr(field, "value", field)
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)) and value and all(isinstance(v, str) for v in value):
        return list(value)
    raise ValueError(f"Invalid field for sorting: {field!r}")


class QuerySetProcessor:
    """Chainable sorting and case-insensitive search over a queryset."""

    def __init__(self, queryset):
        self.queryset = queryset

    def apply_sorting(self, sort_by: dict | None):
        """``sort_by = {"field": <enum or name>, "direction": OrderDirection}``.

        Every ORM field in the enum value is used, so ``["created_at", "pk"]``
        gives a stable order.
        """
        if sort_by and sort_by.get("field") is not None:
            prefix = "-" if _direction_is_desc(sort_by.get("direction")) else ""
            # String names (unlike F()) are validated by order_by() right away, so
            # a field the model lacks fails here instead of during pagination.
            self.queryset = self.queryset.order_by(
                *(f"{prefix}{name}" for name in _sort_fields(sort_by["field"]))
            )
        return self.queryset

    def apply_search(self, search: str | None, search_fields: Sequence[str]):
        """OR an ``icontains`` lookup across ``search_fields``."""
        if search and search_fields:
            query = Q()
            for field in search_fields:
                query |= Q(**{f"{field}__icontains": search})
            self.queryset = self.queryset.filter(query)
        return self.queryset


def resolve_model_list(
    queryset,
    *,
    filter_input=None,
    filters: dict | None = None,
    search: str | None = None,
    search_fields: Sequence[str] = (),
    sort_by: dict | None = None,
    distinct: bool = False,
):
    """Apply filters → search → sorting to ``queryset``.

    ``ValueError`` (e.g. a malformed global ID, or a filter the model doesn't
    support) becomes a ``BAD_REQUEST`` error with its message. Sorting on a
    field the model lacks is ``INVALID_SORT_FIELD``. Any other ``FieldError``
    (a misconfigured search field) is logged and reported without internals.
    """
    try:
        if filters and filter_input is not None:
            queryset = filter_input.filter_queryset(queryset, filters)
        processor = QuerySetProcessor(queryset)
        processor.apply_search(search, search_fields)
        try:
            queryset = processor.apply_sorting(sort_by)
        except FieldError as exc:
            field = getattr(sort_by.get("field"), "name", sort_by.get("field"))
            raise EnhancedGraphQLError(
                message=f"{CommonErrorMessages.INVALID_SORT_FIELD.message} "
                f"`{field}` is not available on {queryset.model.__name__}.",
                message_code=CommonErrorMessages.INVALID_SORT_FIELD.code,
                status=False,
                data=[],
            ) from exc
        if distinct:
            queryset = queryset.distinct()
        return queryset
    except ValueError as exc:
        raise EnhancedGraphQLError(
            message=f"{CommonErrorMessages.BAD_REQUEST.message}: {exc}",
            message_code=CommonErrorMessages.BAD_REQUEST.code,
            status=False,
            data=[],
        ) from exc
    except FieldError as exc:
        logger.error("Invalid list query configuration: %s", exc, exc_info=True)
        raise EnhancedGraphQLError(
            message=CommonErrorMessages.BAD_REQUEST.message,
            message_code=CommonErrorMessages.BAD_REQUEST.code,
            status=False,
            data=[],
        ) from exc


def _plural(node_type) -> str:
    return f"{node_type.__name__.removesuffix('Type').lower()}s"


class ModelListField(FilterConnectionField):
    """A paginated list field that applies ``filters``, ``search`` and ``sortBy`` itself.

    The resolver (optional) only needs to return the *base* queryset — the place
    for permission scoping. Without one, the model's default manager is used::

        class Query(graphene.ObjectType):
            task_lists = ModelListField(
                TaskType,
                filter_input=TaskFilterInput,
                search_fields=["title", "description"],
            )

            @jwt_required
            def resolve_task_lists(root, info, **kwargs):
                return Task.objects.filter(school__in=info.context.user.schools.all())

    Args:
        filter_input: a ``BaseFilterInput`` subclass (adds the ``filters`` argument).
        search_fields: ORM lookups searched with ``icontains`` (adds ``search`` when non-empty).
        sort_input: sorting input type (default ``SortingInput``; ``None`` to disable).
        distinct: call ``.distinct()`` after filtering (for filters spanning to-many joins).
    """

    def __init__(
        self,
        node_type,
        *args,
        filter_input=None,
        search_fields: Sequence[str] = (),
        sort_input=SortingInput,
        distinct: bool = False,
        **kwargs,
    ):
        self.filter_input = filter_input
        self.search_fields = tuple(search_fields)
        self.distinct = distinct
        plural = _plural(node_type)
        if filter_input is not None:
            kwargs.setdefault(
                "filters", filter_input(description=f"Filtering options for {plural}.")
            )
        if sort_input is not None:
            kwargs.setdefault("sort_by", sort_input(description=f"Sort {plural}."))
        if self.search_fields:
            kwargs.setdefault(
                "search", graphene.String(description=f"Search {plural} by this value.")
            )
        super().__init__(node_type, *args, **kwargs)

    def process_iterable(self, iterable, info, args):
        return resolve_model_list(
            iterable,
            filter_input=self.filter_input,
            filters=args.get("filters"),
            search=args.get("search"),
            search_fields=self.search_fields,
            sort_by=args.get("sort_by"),
            distinct=self.distinct,
        )
