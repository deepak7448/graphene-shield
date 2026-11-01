"""Batch-load related rows for list fields, with the same results graphene-django gives.

**Why.** When a node type overrides ``get_queryset`` (soft-delete, tenancy, or just
``graphene-django-optimizer``'s ``OptimizedDjangoObjectType``, which
``CountableDjangoObjectType`` uses), graphene-django resolves every foreign key and
one-to-one field pointing at it with a separate query per row (``get_node``),
ignoring ``select_related``. A 50-row page with three such fields costs 150 extra
queries.

**How.** ``relation_prefetch`` loads the relation for the whole page in one query,
through the target type's ``get_queryset``, exactly as ``get_node`` would, so rows
that ``get_queryset`` hides still come back as ``null``. ``relation_resolver`` reads
the prefetched value and otherwise falls back to graphene-django's own per-row
lookup, so detail queries behave as before. ``SelectionPrefetcher`` decides which
prefetches a page needs from the fields the client selected::

    class ArticleType(CountableDjangoObjectType):
        class Meta:
            model = Article
            fields = ("id", "title", "author", "stats")

        prefetch_for_selection = SelectionPrefetcher(
            Article, {"author": related("author"), "stats": related("stats")}
        )
        resolve_author = relation_resolver("author")
        resolve_stats = relation_resolver("stats")

The lookups mirror graphene-django 3.2's ``convert_field_to_djangomodel`` and
``convert_onetoone_field_to_djangomodel`` resolvers, using only its public API.
Types are looked up in graphene-django's global registry.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from django.db.models import Model, Prefetch
from graphene_django.registry import get_global_registry
from graphene_django.types import DjangoObjectType
from graphene_django.utils import bypass_get_queryset

from graphene_shield.selection import collect_selected_fields, nested_node_fields

Lookup = str | Prefetch


def registered_type(model: type[Model]):
    """The ``DjangoObjectType`` graphene-django uses for relation fields pointing at ``model``."""
    return get_global_registry().get_type_for_model(model)


def _overrides_get_queryset(node_type) -> bool:
    # The condition graphene-django uses to decide between attribute access and get_node.
    return node_type.get_queryset.__func__ is not DjangoObjectType.get_queryset.__func__


def _guarded_type(model: type[Model]):
    """The target's registered type if graphene-django loads it via ``get_queryset``, else None."""
    node_type = registered_type(model)
    if node_type is None or not _overrides_get_queryset(node_type):
        return None
    return node_type


def _default_to_attr(field_name: str) -> str:
    return f"prefetched_{field_name}"


def relation_prefetch(
    model: type[Model],
    field_name: str,
    info,
    *,
    through: str | None = None,
    to_attr: str | None = None,
) -> Prefetch:
    """``Prefetch`` of a foreign key or one-to-one field (either side), loaded like graphene-django.

    If the target's registered type overrides ``get_queryset``, rows go through it
    over ``Model.objects`` (what ``get_node`` does); otherwise through the base
    manager (what attribute access does). The result is stored in ``to_attr``
    (default ``prefetched_<field_name>``), which ``relation_resolver`` reads.

    ``through`` is the lookup path from the queryset being prefetched to ``model``
    (e.g. ``"goods_items"``), or None when the queryset is of ``model`` itself.
    """
    field = model._meta.get_field(field_name)
    if not (field.many_to_one or field.one_to_one):
        raise ValueError(
            f"{model.__name__}.{field_name} must be a one-to-one or many-to-one relation; "
            "prefetch to-many relations with a plain Prefetch."
        )
    target = field.related_model
    node_type = _guarded_type(target)
    if node_type is not None:
        queryset = node_type.get_queryset(target.objects.all(), info)
    else:
        queryset = target._base_manager.all()
    lookup = f"{through}__{field_name}" if through else field_name
    return Prefetch(lookup, queryset=queryset, to_attr=to_attr or _default_to_attr(field_name))


def relation_resolver(field_name: str, *, to_attr: str | None = None) -> Callable:
    """Resolver for a foreign key or one-to-one field that uses ``relation_prefetch``'s result.

    Without a prefetch it does exactly what graphene-django's resolver does: attribute
    access when the target type doesn't override ``get_queryset``, otherwise
    ``get_node`` (forward relations) or a ``get_queryset`` lookup (reverse one-to-one).
    Assign it in the type body: ``resolve_author = relation_resolver("author")``.
    """
    attr = to_attr or _default_to_attr(field_name)

    @bypass_get_queryset
    def resolver(root, info, **kwargs):
        if hasattr(root, attr):
            return getattr(root, attr)
        field = root._meta.get_field(field_name)
        node_type = _guarded_type(field.related_model)
        if node_type is None:
            # A missing reverse one-to-one raises a DoesNotExist that is also an
            # AttributeError, so the default resolves it to None too.
            return getattr(root, field_name, None)
        if field.concrete:  # forward foreign key / one-to-one
            return node_type.get_node(info, getattr(root, field.attname))
        target = node_type._meta.model
        queryset = target.objects.filter(**{field.remote_field.name: root.pk})
        try:
            return node_type.get_queryset(queryset, info).get()
        except target.DoesNotExist:
            return None

    resolver.__name__ = f"resolve_{field_name}"
    return resolver


def under_path(prefix: str, lookup: Lookup) -> Lookup:
    """The same lookup one relation deeper: ``"x"`` -> ``"<prefix>__x"``.

    A ``Prefetch`` is rebuilt with the prefixed path and the same queryset and ``to_attr``.
    """
    if isinstance(lookup, str):
        return f"{prefix}__{lookup}"
    return Prefetch(
        f"{prefix}__{lookup.prefetch_through}", queryset=lookup.queryset, to_attr=lookup.to_attr
    )


class related:
    """Builder: ``relation_prefetch`` of one field of the prefetcher's model."""

    def __init__(self, field_name: str, *, to_attr: str | None = None):
        self.field_name = field_name
        self.to_attr = to_attr

    def build(self, model, selection, info) -> list[Lookup]:
        return [relation_prefetch(model, self.field_name, info, to_attr=self.to_attr)]


class nested:
    """Builder: follow ``relation``, then run another prefetcher for the fields selected beneath it.

    A foreign key or one-to-one ``relation`` is loaded with ``relation_prefetch`` and
    the inner prefetches run on the objects stored in its ``to_attr``, so resolve
    that field with ``relation_resolver(relation)``. A to-many relation is followed
    as a plain lookup.

    ``connection=True`` when the field is a nested connection (row fields are read
    from ``edges { node }`` / ``items``) rather than a single object or plain list.
    """

    def __init__(self, relation: str, prefetcher: SelectionPrefetcher, *, connection=False):
        self.relation = relation
        self.prefetcher = prefetcher
        self.connection = connection

    def build(self, model, selection, info) -> list[Lookup]:
        if self.connection:
            fields = nested_node_fields(info, selection)
        else:
            fields = collect_selected_fields(info, selection)
        inner = self.prefetcher.prefetches(fields, info)
        field = model._meta.get_field(self.relation)
        if field.many_to_one or field.one_to_one:
            first = relation_prefetch(model, self.relation, info)
            path = first.to_attr
        else:
            first = path = self.relation
        return [first, *(under_path(path, lookup) for lookup in inner)]


Builder = Any  # str | Prefetch | related | nested | callable(selection, info) | list of these


class SelectionPrefetcher:
    """Prefetches for one model's list pages, chosen by the GraphQL fields the client selected.

    ``builders`` maps a GraphQL field name (camelCase, as selected) to what that field
    needs: a lookup string, a ``Prefetch``, ``related(...)``, ``nested(...)``, a
    callable ``(selection, info) -> lookups`` (``selection`` is that field's
    sub-selection sets, for ``collect_selected_fields``), or a list of these.
    Prefetches are emitted in ``builders`` order.

    Instances are callable as ``prefetcher(queryset, node_fields, info)``, so one can be
    assigned directly as a node type's ``prefetch_for_selection``.
    """

    def __init__(self, model: type[Model], builders: Mapping[str, Builder]):
        self.model = model
        self.builders = dict(builders)

    def prefetches(self, node_fields: Mapping[str, Any], info) -> list[Lookup]:
        result: list[Lookup] = []
        for field, builder in self.builders.items():
            if field in node_fields:
                result.extend(self._build(builder, node_fields[field], info))
        return result

    def _build(self, builder: Builder, selection, info) -> Iterable[Lookup]:
        if isinstance(builder, (str, Prefetch)):
            return [builder]
        if isinstance(builder, (list, tuple)):
            return [lookup for item in builder for lookup in self._build(item, selection, info)]
        if hasattr(builder, "build"):
            return builder.build(self.model, selection, info)
        if callable(builder):
            return list(builder(selection, info))
        raise TypeError(f"Unsupported prefetch builder: {builder!r}")

    def __call__(self, queryset, node_fields, info):
        lookups = self.prefetches(node_fields, info)
        return queryset.prefetch_related(*lookups) if lookups else queryset
