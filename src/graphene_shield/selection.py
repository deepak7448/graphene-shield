"""Which fields did the client select? Helpers for selection-driven query planning.

Used by ``FilterConnectionField`` to skip fetching rows nobody reads and to pass the
selected node fields to ``prefetch_for_selection``; useful in your own resolvers too.

Results map *schema* field names (aliases are ignored) to the list of sub-selection
sets selected under that name, so ``a: name`` and ``b: name`` both count as ``name``
and their sub-selections are merged.
"""

from __future__ import annotations

from collections.abc import Iterable

from graphql import GraphQLIncludeDirective, GraphQLResolveInfo, GraphQLSkipDirective
from graphql.execution.values import get_directive_values
from graphql.language import (
    FieldNode,
    FragmentSpreadNode,
    InlineFragmentNode,
    SelectionSetNode,
)

SelectedFields = dict[str, list[SelectionSetNode | None]]


def _included(info: GraphQLResolveInfo, node) -> bool:
    """Evaluate ``@skip`` / ``@include`` the way the executor does."""
    variables = info.variable_values
    skip = get_directive_values(GraphQLSkipDirective, node, variables)
    if skip and skip["if"] is True:
        return False
    include = get_directive_values(GraphQLIncludeDirective, node, variables)
    return not (include and include["if"] is False)


def collect_selected_fields(
    info: GraphQLResolveInfo, selection_sets: Iterable[SelectionSetNode | None]
) -> SelectedFields:
    """Map field name -> sub-selection sets selected directly in ``selection_sets``.

    Named fragments and inline fragments are expanded; ``@skip``/``@include`` are
    honoured. Type conditions on fragments are not checked, so on unions and
    interfaces a field may be reported for a type that doesn't have it: callers
    should treat the result as "possibly needed", which only ever means extra
    work, never missing data.

    Each named fragment is expanded at most once per call (as graphql-core does),
    so repeated spreads can't make the walk exponential.
    """
    selected: SelectedFields = {}
    visited_fragments: set[str] = set()
    stack = [s for s in selection_sets if s is not None]
    while stack:
        selection_set = stack.pop()
        for selection in selection_set.selections:
            if not _included(info, selection):
                continue
            if isinstance(selection, FieldNode):
                selected.setdefault(selection.name.value, []).append(selection.selection_set)
            elif isinstance(selection, InlineFragmentNode):
                stack.append(selection.selection_set)
            elif isinstance(selection, FragmentSpreadNode):
                name = selection.name.value
                if name in visited_fragments:
                    continue
                visited_fragments.add(name)
                fragment = info.fragments.get(name)
                if fragment is not None:
                    stack.append(fragment.selection_set)
    return selected


def _node_fields(info: GraphQLResolveInfo, connection_fields: SelectedFields) -> SelectedFields:
    edge_fields = collect_selected_fields(info, connection_fields.get("edges", []))
    return collect_selected_fields(
        info, [*edge_fields.get("node", []), *connection_fields.get("items", [])]
    )


def connection_node_selection(info: GraphQLResolveInfo) -> tuple[SelectedFields, SelectedFields]:
    """``(connection_fields, node_fields)`` for the list field being resolved.

    ``node_fields`` are the fields selected on each row: under ``edges { node }`` for
    Relay connections, or under ``items`` for page-number results. It is empty
    when no row fields are selected (e.g. only ``totalCount``).
    """
    connection_fields = collect_selected_fields(
        info, [node.selection_set for node in info.field_nodes]
    )
    return connection_fields, _node_fields(info, connection_fields)


def nested_node_fields(
    info: GraphQLResolveInfo, selection_sets: Iterable[SelectionSetNode | None]
) -> SelectedFields:
    """Row fields of a *nested* connection, given that field's sub-selections.

    For ``node_fields["reviews"]`` it returns what is selected under
    ``reviews { edges { node { ... } } }`` (or ``reviews { items { ... } }``).
    """
    return _node_fields(info, collect_selected_fields(info, selection_sets))
