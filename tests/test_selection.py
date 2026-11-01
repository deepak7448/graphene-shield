import graphene

from graphene_shield.selection import (
    collect_selected_fields,
    connection_node_selection,
    nested_node_fields,
)


def _shape(fields):
    return sorted(fields)


class Inner(graphene.ObjectType):
    a = graphene.String()
    b = graphene.String()


class Node(graphene.ObjectType):
    name = graphene.String()
    author = graphene.String()
    inner = graphene.Field(Inner)
    children = graphene.Field(lambda: Connection)


class Edge(graphene.ObjectType):
    node = graphene.Field(Node)
    cursor = graphene.String()


class Connection(graphene.ObjectType):
    edges = graphene.List(Edge)
    items = graphene.List(Node)
    total_count = graphene.Int()
    page_info = graphene.String()


class Query(graphene.ObjectType):
    """`connection` records what the selection helpers saw in `info.context`."""

    connection = graphene.Field(Connection)

    def resolve_connection(root, info):
        connection_fields, node_fields = connection_node_selection(info)
        children = nested_node_fields(info, node_fields.get("children", []))
        info.context["seen"] = {
            "connection": _shape(connection_fields),
            "node": _shape(node_fields),
            "inner_subfields": _shape(collect_selected_fields(info, node_fields.get("inner", []))),
            "nested_node": _shape(children),
        }
        return None


schema = graphene.Schema(query=Query)


def seen(query, variables=None):
    context = {}
    result = schema.execute(query, variable_values=variables, context_value=context)
    assert result.errors is None, result.errors
    return context["seen"]


def test_reports_connection_and_node_fields():
    got = seen("{ connection { totalCount edges { cursor node { name author } } } }")
    assert got["connection"] == ["edges", "totalCount"]
    assert got["node"] == ["author", "name"]


def test_aliases_report_schema_field_names():
    got = seen("{ connection { t: totalCount e: edges { n: node { x: name } } } }")
    assert got["connection"] == ["edges", "totalCount"]
    assert got["node"] == ["name"]


def test_named_and_inline_fragments_are_expanded():
    got = seen(
        """
        query { connection { ...Conn } }
        fragment Conn on Connection { edges { node { ...NodeBits ... on Node { author } } } }
        fragment NodeBits on Node { name }
        """
    )
    assert got["connection"] == ["edges"]
    assert got["node"] == ["author", "name"]


def test_page_mode_items_count_as_node_fields():
    got = seen("{ connection { items { name } } }")
    assert got["node"] == ["name"]


def test_count_only_selection_has_no_node_fields():
    got = seen("{ connection { totalCount } }")
    assert got == {
        "connection": ["totalCount"],
        "node": [],
        "inner_subfields": [],
        "nested_node": [],
    }


def test_skip_and_include_directives_are_honoured():
    got = seen(
        """
        query ($skipName: Boolean!, $withAuthor: Boolean!) {
          connection { edges { node {
            name @skip(if: $skipName)
            author @include(if: $withAuthor)
            inner @include(if: true) { a }
          } } }
        }
        """,
        {"skipName": True, "withAuthor": False},
    )
    assert got["node"] == ["inner"]


def test_skipped_fragment_contributes_nothing():
    got = seen(
        """
        query { connection { edges { node { name ...Extra @skip(if: true) } } } }
        fragment Extra on Node { author }
        """
    )
    assert got["node"] == ["name"]


def test_sub_selections_of_a_field_are_collected():
    got = seen("{ connection { edges { node { inner { a b } } } } }")
    assert got["inner_subfields"] == ["a", "b"]


def test_fields_from_several_occurrences_are_merged():
    got = seen("{ connection { edges { node { name } } edges { node { author } } } }")
    assert got["node"] == ["author", "name"]


def test_self_referencing_fragments_terminate():
    # Each named fragment is expanded at most once per call, so mutually spreading
    # fragments can't make the walk exponential. Graphql validation rejects real
    # cycles, so build the cycle-like shape with repeated spreads instead.
    spreads = " ".join("...F" for _ in range(50))
    got = seen(
        f"query {{ connection {{ edges {{ node {{ {spreads} }} }} }} }} fragment F on Node {{ name }}"
    )
    assert got["node"] == ["name"]


def test_nested_node_fields_reads_a_nested_connection():
    got = seen(
        "{ connection { edges { node { children { totalCount edges { node { author } } } } } } }"
    )
    assert got["nested_node"] == ["author"]
