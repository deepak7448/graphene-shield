from __future__ import annotations

import graphene
from graphene.types.enum import Enum


class BaseEnum(Enum):
    """Graphene enum accepting an optional ``doc_category`` Meta option."""

    @classmethod
    def __init_subclass_with_meta__(cls, enum=None, _meta=None, doc_category=None, **options):
        cls.doc_category = doc_category
        super().__init_subclass_with_meta__(enum=enum, _meta=_meta, **options)


class OrderDirection(graphene.Enum):
    ASC = ""
    DESC = "-"

    @property
    def description(self):
        if self == OrderDirection.ASC:
            return "Specifies an ascending sort order."
        if self == OrderDirection.DESC:
            return "Specifies a descending sort order."
        raise ValueError(f"Unsupported enum value: {self.value}")


class QuerysetSortingInput(graphene.InputObjectType):
    direction = graphene.Argument(
        OrderDirection, required=True, description="The direction in which to sort."
    )


def make_sort_field_enum(name: str, fields: dict[str, list[str] | str]) -> type[graphene.Enum]:
    """Build a sort-field enum. Each value lists the ORM fields to order by, in order.

    >>> TaskSortField = make_sort_field_enum("TaskSortField", {
    ...     "CREATED_AT": ["created_at", "pk"],
    ...     "TITLE": "title",
    ... })
    """
    members = {
        key: [value] if isinstance(value, str) else list(value) for key, value in fields.items()
    }

    def description(self):
        return f"Sort by {self.name.replace('_', ' ').lower()}."

    return type(
        name,
        (BaseEnum,),
        {**members, "description": property(description)},
    )


def make_sorting_input(name: str, field_enum: type[graphene.Enum]) -> type[QuerysetSortingInput]:
    """Build ``<name>`` input with ``field: field_enum!`` and ``direction: OrderDirection!``."""
    return type(
        name,
        (QuerysetSortingInput,),
        {
            "field": graphene.Argument(
                field_enum, required=True, description="The field to sort by."
            )
        },
    )


SortField = make_sort_field_enum(
    "SortField",
    {
        "ID": ["pk"],
        "CREATED_AT": ["created_at", "pk"],
        "UPDATED_AT": ["updated_at", "pk"],
        "NAME": ["name", "pk"],
    },
)
SortingInput = make_sorting_input("SortingInput", SortField)
