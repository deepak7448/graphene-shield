"""Relay connections with non-null edges, ``totalCount`` and offset pagination."""

from __future__ import annotations

from functools import partial

import graphene
from django.core.exceptions import ImproperlyConfigured
from django.db.models.manager import Manager
from django.db.models.query import QuerySet
from graphene import Int, NonNull, relay
from graphene.relay import ConnectionField
from graphene.relay.connection import connection_adapter, page_info_adapter
from graphene_django.types import DjangoObjectType
from graphql_relay import cursor_to_offset, get_offset_with_default, offset_to_cursor
from graphql_relay.connection.array_connection import connection_from_array_slice

from graphene_shield import pagination
from graphene_shield.conf import shield_settings
from graphene_shield.messages import CommonErrorMessages
from graphene_shield.responses import EnhancedGraphQLError

try:
    from graphene_django_optimizer import OptimizedDjangoObjectType as _BaseObjectType
    from graphene_django_optimizer import query as optimize_query
except ImportError:  # pragma: no cover - exercised only without the extra
    _BaseObjectType = DjangoObjectType
    optimize_query = None

try:
    from promise import Promise
except ImportError:  # pragma: no cover
    Promise = None


class NonNullConnection(relay.Connection):
    """Connection whose ``edges`` and ``edges.node`` are non-null."""

    class Meta:
        abstract = True

    @classmethod
    def __init_subclass_with_meta__(cls, node=None, name=None, **options):
        super().__init_subclass_with_meta__(node=node, name=name, **options)

        class EdgeBase:
            node = graphene.Field(
                cls._meta.node, description="The item at the end of the edge.", required=True
            )
            cursor = graphene.String(required=True, description="A cursor for use in pagination.")

        cls.Edge = type(cls.Edge._meta.name, (EdgeBase, graphene.ObjectType), {})
        cls._meta.fields["edges"] = graphene.Field(
            graphene.NonNull(graphene.List(graphene.NonNull(cls.Edge)))
        )


class CountableConnection(NonNullConnection):
    """Adds ``totalCount`` — the size of the full result set, not just the page."""

    class Meta:
        abstract = True

    total_count = graphene.Int(description="Total number of items across all pages.")

    @staticmethod
    def resolve_total_count(root, info):
        length = getattr(root, "length", None)
        if length is not None:
            return length
        iterable = getattr(root, "iterable", None)
        if iterable is None:
            return 0
        return iterable.count() if isinstance(iterable, QuerySet) else len(iterable)


class CountableDjangoObjectType(_BaseObjectType):
    """``DjangoObjectType`` that gets a ``<Name>CountableConnection`` automatically.

    Also exposes ``number`` — the raw primary key — next to the Relay ``id``.
    Uses ``graphene-django-optimizer`` when installed.
    """

    number = graphene.Int(description="Database primary key.")

    class Meta:
        abstract = True

    @classmethod
    def __init_subclass_with_meta__(cls, *args, connection=None, **kwargs):
        if connection is None:
            connection = CountableConnection.create_type(
                f"{cls.__name__}CountableConnection", node=cls
            )
        super().__init_subclass_with_meta__(*args, connection=connection, **kwargs)

    @staticmethod
    def resolve_number(root, info):
        return root.pk


def maybe_queryset(value):
    if isinstance(value, Manager):
        return value.get_queryset()
    return value


def _pagination_error(error, detail: str):
    return EnhancedGraphQLError(
        message=f"{error.message} {detail}".strip(),
        message_code=error.code,
        status=False,
        data=[],
    )


class FilterConnectionField(ConnectionField):
    """List field for ``DjangoObjectType`` nodes with a choice of pagination and a size cap.

    Args:
        pagination: ``"offset"``, ``"keyset"`` or ``"page"`` (default: the
            ``PAGINATION`` setting). See ``graphene_shield.pagination``.
        on: name of the model manager to fall back to (default: ``_default_manager``).
        max_limit: maximum ``first``/``last``/``pageSize`` (default
            ``CONNECTION_MAX_LIMIT``); also the page size for connections when
            neither ``first`` nor ``last`` is given. ``None`` disables it.
        enforce_first_or_last: require ``first`` or ``last`` (connection modes only).
    """

    _UNSET = object()

    def __init__(self, *args, **kwargs):
        self.on = kwargs.pop("on", None)
        max_limit = kwargs.pop("max_limit", self._UNSET)
        self.max_limit = (
            shield_settings.CONNECTION_MAX_LIMIT if max_limit is self._UNSET else max_limit
        )
        self.enforce_first_or_last = kwargs.pop("enforce_first_or_last", False)
        self.pagination = kwargs.pop("pagination", None) or shield_settings.PAGINATION
        if self.pagination not in pagination.PAGINATION_MODES:
            raise ImproperlyConfigured(
                f"Unknown pagination {self.pagination!r}; "
                f"use one of {', '.join(pagination.PAGINATION_MODES)}."
            )
        if self.pagination == "offset":
            kwargs.setdefault("offset", Int(description="Number of items to skip."))
        elif self.pagination == "page":
            kwargs.setdefault(
                "page", Int(default_value=1, description="Page number, starting at 1.")
            )
            kwargs.setdefault(
                "page_size",
                Int(description=f"Items per page (default {self.default_page_size})."),
            )
        super().__init__(*args, **kwargs)
        if self.pagination == "page":
            for name in ("first", "last", "after", "before"):
                self.args.pop(name, None)

    @property
    def default_page_size(self) -> int:
        size = shield_settings.DEFAULT_PAGE_SIZE
        return min(size, self.max_limit) if self.max_limit else size

    @property
    def type(self):
        _type = super(ConnectionField, self).type
        non_null = isinstance(_type, NonNull)
        if non_null:
            _type = _type.of_type
        if not (isinstance(_type, type) and issubclass(_type, DjangoObjectType)):
            raise TypeError(f"{type(self).__name__} only accepts DjangoObjectType types")
        if self.pagination == "page":
            return NonNull(pagination.page_type_for(_type))
        if not _type._meta.connection:
            raise TypeError(f"The type {_type.__name__} doesn't have a connection")
        connection_type = _type._meta.connection
        return NonNull(connection_type) if non_null else connection_type

    @property
    def connection_type(self):
        _type = self.type
        return _type.of_type if isinstance(_type, NonNull) else _type

    @property
    def node_type(self):
        _type = super(ConnectionField, self).type
        return _type.of_type if isinstance(_type, NonNull) else _type

    @property
    def model(self):
        return self.node_type._meta.model

    def get_manager(self):
        if self.on:
            return getattr(self.model, self.on)
        return self.model._default_manager

    # ── Resolution ──

    def validate_pagination(self, info, args):
        name = info.field_name
        if self.pagination == "page":
            page, page_size = args.get("page"), args.get("page_size")
            if page is not None and page < 1:
                raise _pagination_error(
                    CommonErrorMessages.BAD_REQUEST, "`page` must be 1 or more."
                )
            if page_size is not None and page_size < 1:
                raise _pagination_error(
                    CommonErrorMessages.BAD_REQUEST, "`pageSize` must be 1 or more."
                )
            if self.max_limit and page_size is not None and page_size > self.max_limit:
                raise _pagination_error(
                    CommonErrorMessages.PAGINATION_LIMIT_EXCEEDED,
                    f"`pageSize` on `{name}` must be at most {self.max_limit}.",
                )
            return

        first, last = args.get("first"), args.get("last")
        offset, before = args.get("offset"), args.get("before")
        if self.enforce_first_or_last and first is None and last is None:
            raise _pagination_error(CommonErrorMessages.PAGINATION_REQUIRED, f"(`{name}`)")
        if self.max_limit:
            for key, value in (("first", first), ("last", last)):
                if value is not None and value > self.max_limit:
                    raise _pagination_error(
                        CommonErrorMessages.PAGINATION_LIMIT_EXCEEDED,
                        f"`{key}` on `{name}` must be at most {self.max_limit}.",
                    )
        for key, value in (("first", first), ("last", last), ("offset", offset)):
            if value is not None and value < 0:
                raise _pagination_error(
                    CommonErrorMessages.BAD_REQUEST, f"`{key}` must be non-negative."
                )
        if offset is not None and before is not None:
            raise _pagination_error(CommonErrorMessages.PAGINATION_CONFLICT, f"(`{name}`)")
        if self.pagination == "offset":
            # Keyset cursors are checked when decoded.
            for key in ("after", "before"):
                cursor = args.get(key)
                if cursor is not None and cursor_to_offset(cursor) is None:
                    raise _pagination_error(
                        CommonErrorMessages.BAD_REQUEST, f"`{key}` is not a valid cursor."
                    )

    def resolve_queryset(self, connection, queryset, info, args):
        queryset = maybe_queryset(queryset)
        if optimize_query is not None and isinstance(queryset, QuerySet):
            queryset = optimize_query(queryset, info=info)
        return self.node_type.get_queryset(queryset, info)

    def process_iterable(self, iterable, info, args):
        """Hook run on the resolver's result before pagination. Override in subclasses."""
        return iterable

    @classmethod
    def resolve_connection(cls, connection, args, iterable, max_limit=None):
        """Offset pagination."""
        args = dict(args)
        offset = args.pop("offset", None)
        after = args.get("after")
        if offset:
            if after:
                offset += cursor_to_offset(after) + 1
            args["after"] = offset_to_cursor(offset - 1)

        iterable = maybe_queryset(iterable)
        if max_limit is not None and args.get("first") is None and args.get("last") is None:
            args["first"] = max_limit

        # Paging forwards needs no COUNT: fetch one extra row to learn hasNextPage.
        if args.get("first") is not None and args.get("last") is None and not args.get("before"):
            return pagination.offset_forward_connection(
                connection, iterable, args.get("after"), args["first"]
            )

        # `last`/`before` are relative to the end, so the total length is needed.
        if isinstance(iterable, QuerySet):
            array_length = iterable.count()
        elif hasattr(iterable, "__len__"):
            array_length = len(iterable)
        else:
            raise TypeError(f"Expected a QuerySet or sized iterable, got {type(iterable).__name__}")

        # Clamp so Django never sees a negative slice.
        slice_start = min(get_offset_with_default(args.get("after"), -1) + 1, array_length)
        connection = connection_from_array_slice(
            iterable[slice_start:],
            args,
            slice_start=slice_start,
            array_length=array_length,
            array_slice_length=array_length - slice_start,
            connection_type=partial(connection_adapter, connection),
            edge_type=connection.Edge,
            page_info_type=page_info_adapter,
        )
        connection.iterable = iterable
        connection.length = array_length
        return connection

    def paginate(self, connection, args, iterable):
        iterable = pagination.ensure_ordered(maybe_queryset(iterable))
        if self.pagination == "page":
            return pagination.page_result(
                iterable, args.get("page") or 1, args.get("page_size") or self.default_page_size
            )
        if self.pagination == "keyset":
            return pagination.keyset_connection(connection, iterable, args, self.max_limit)
        return self.resolve_connection(connection, args, iterable, max_limit=self.max_limit)

    def connection_resolver(self, resolver, connection, root, info, **args):
        self.validate_pagination(info, args)

        iterable = resolver(root, info, **args)
        if iterable is None:
            iterable = self.get_manager()

        def finish(result):
            result = self.resolve_queryset(connection, result, info, args)
            result = self.process_iterable(result, info, args)
            return self.paginate(connection, args, result)

        if Promise is not None and Promise.is_thenable(iterable):
            return Promise.resolve(iterable).then(finish)
        return finish(iterable)

    def wrap_resolve(self, parent_resolver):
        connection = None if self.pagination == "page" else self.connection_type
        return partial(self.connection_resolver, parent_resolver, connection)
