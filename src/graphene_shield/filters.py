from __future__ import annotations

import graphene

from graphene_shield.utils import resolve_global_id


class IdFilterInput(graphene.InputObjectType):
    """Only an ``id`` filter; works on any model (matches the primary key, whatever its name)."""

    id = graphene.ID(description="Filter by global Relay ID (exact match).")

    @classmethod
    def filter_queryset(cls, queryset, filters):
        return cls._apply_common_filters(queryset, filters)

    @classmethod
    def _apply_common_filters(cls, queryset, filters):
        if global_id := filters.get("id"):
            queryset = queryset.filter(pk=resolve_global_id(global_id))
        return queryset


class BaseFilterInput(IdFilterInput):
    """``id``, ``isActive``, ``createdAt`` and ``updatedAt`` filters.

    Subclasses add fields and override ``filter_queryset``::

        class TaskFilterInput(BaseFilterInput):
            title = graphene.String()

            @classmethod
            def filter_queryset(cls, queryset, filters):
                if title := filters.get("title"):
                    queryset = queryset.filter(title__icontains=title)
                return super().filter_queryset(queryset, filters)

    If the model names these columns differently, remap them::

        class LegacyFilterInput(BaseFilterInput):
            common_field_map = {"is_active": "enabled", "created_at": "created"}

    Using a filter whose column the model doesn't have is a ``BAD_REQUEST``
    (rather than being ignored and returning unfiltered rows). For models with
    none of these columns, use ``IdFilterInput`` so the arguments aren't exposed.
    """

    is_active = graphene.Boolean(description="Filter by active status.")
    created_at = graphene.Date(description="Filter by created date.")
    updated_at = graphene.Date(description="Filter by updated date.")

    # filter argument -> model field
    common_field_map: dict[str, str] = {}

    # filter argument -> name in the schema, for error messages
    _schema_names = {"is_active": "isActive", "created_at": "createdAt", "updated_at": "updatedAt"}

    @classmethod
    def _model_field(cls, queryset, argument: str) -> str:
        field = cls.common_field_map.get(argument, argument)
        if field not in {f.name for f in queryset.model._meta.get_fields()}:
            raise ValueError(
                f"Filter `{cls._schema_names[argument]}` is not supported for "
                f"{queryset.model.__name__}."
            )
        return field

    @classmethod
    def _apply_common_filters(cls, queryset, filters):
        queryset = super()._apply_common_filters(queryset, filters)
        if (is_active := filters.get("is_active")) is not None:
            queryset = queryset.filter(**{cls._model_field(queryset, "is_active"): is_active})
        for argument in ("created_at", "updated_at"):
            if value := filters.get(argument):
                field = cls._model_field(queryset, argument)
                queryset = queryset.filter(**{f"{field}__date": value})
        return queryset
