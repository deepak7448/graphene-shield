import pytest
from graphql_relay import to_global_id

from tests.testapp.models import Book, LegacyItem, Tag

LIST = """
query($first: Int, $offset: Int, $search: String, $filters: BookFilterInput, $sortBy: BookSortingInput) {
  bookLists(first: $first, offset: $offset, search: $search, filters: $filters, sortBy: $sortBy) {
    totalCount
    pageInfo { hasNextPage }
    edges { node { name number } }
  }
}
"""


@pytest.fixture
def books(db):
    data = [
        ("Alpha", "Ann", 3, True),
        ("Bravo", "Bob", 5, True),
        ("Charlie", "Ann", 5, True),
        ("Delta", "Dan", 1, False),
        ("Echo", "Eve", 4, True),
        ("Foxtrot", "Fay", 2, True),
        ("Golf", "Gus", 2, True),
    ]
    return [Book.objects.create(name=n, author=a, rating=r, is_active=act) for n, a, r, act in data]


def names(result):
    assert result.errors is None, result.errors
    return [e["node"]["name"] for e in result.data["bookLists"]["edges"]]


def test_default_page_size_is_max_limit_and_total_count_is_full(execute, books):
    result = execute(LIST)
    assert len(names(result)) == 5
    assert result.data["bookLists"]["totalCount"] == 7
    assert result.data["bookLists"]["pageInfo"]["hasNextPage"] is True


def test_offset(execute, books):
    result = execute(
        LIST, {"first": 2, "offset": 2, "sortBy": {"field": "NAME", "direction": "ASC"}}
    )
    assert names(result) == ["Charlie", "Delta"]


def test_first_above_max_limit_is_a_coded_error(execute, books):
    result = execute(LIST, {"first": 6})
    assert result.errors[0].extensions["message_code"] == "PAGINATION_LIMIT_EXCEEDED"


def test_negative_offset_rejected(execute, books):
    result = execute(LIST, {"offset": -1})
    assert result.errors[0].extensions["message_code"] == "BAD_REQUEST"


def test_multi_field_sort_uses_tie_breaker(execute, books):
    result = execute(LIST, {"first": 3, "sortBy": {"field": "RATING", "direction": "DESC"}})
    # rating DESC, then name DESC as tie-breaker for Bravo/Charlie
    assert names(result) == ["Charlie", "Bravo", "Echo"]


def test_search_and_filters(execute, books):
    assert names(
        execute(LIST, {"search": "ann", "sortBy": {"field": "NAME", "direction": "ASC"}})
    ) == [
        "Alpha",
        "Charlie",
    ]
    result = execute(
        LIST,
        {
            "filters": {"minRating": 4, "isActive": True},
            "sortBy": {"field": "NAME", "direction": "ASC"},
        },
    )
    assert names(result) == ["Bravo", "Charlie", "Echo"]


def test_filter_by_global_id(execute, books):
    gid = to_global_id("BookType", books[1].pk)
    assert names(execute(LIST, {"filters": {"id": gid}})) == ["Bravo"]


def test_malformed_global_id_is_bad_request(execute, books):
    result = execute(LIST, {"filters": {"id": "not-a-global-id"}})
    assert result.errors[0].extensions["message_code"] == "BAD_REQUEST"


def test_custom_resolver_scopes_base_queryset(execute, books):
    result = execute("{ activeBookLists { totalCount } }")
    assert result.data["activeBookLists"]["totalCount"] == 6


def test_filter_on_missing_field_is_bad_request_not_ignored(execute, db):
    Tag.objects.create(label="x")
    result = execute("{ tagLists(filters: {isActive: false}) { totalCount } }")
    assert result.errors[0].extensions["message_code"] == "BAD_REQUEST"
    assert "`isActive` is not supported for Tag" in result.errors[0].message


def test_id_filter_works_without_audit_fields(execute, db):
    tag = Tag.objects.create(label="x")
    Tag.objects.create(label="y")
    gid = to_global_id("TagType", tag.pk)
    result = execute(f'{{ idOnlyTagLists(filters: {{id: "{gid}"}}) {{ totalCount }} }}')
    assert result.errors is None
    assert result.data["idOnlyTagLists"]["totalCount"] == 1


def test_id_only_input_does_not_expose_audit_filters(execute, db):
    result = execute("{ idOnlyTagLists(filters: {isActive: true}) { totalCount } }")
    assert "isActive" in result.errors[0].message  # schema validation error


def test_common_field_map_remaps_columns(execute, db):
    LegacyItem.objects.create(title="on", enabled=True)
    LegacyItem.objects.create(title="off", enabled=False)
    result = execute("{ legacyItemLists(filters: {isActive: false}) { edges { node { title } } } }")
    assert result.errors is None
    assert [e["node"]["title"] for e in result.data["legacyItemLists"]["edges"]] == ["off"]
    # updated_at is not mapped and the model has no such column
    result = execute('{ legacyItemLists(filters: {updatedAt: "2026-01-01"}) { totalCount } }')
    assert result.errors[0].extensions["message_code"] == "BAD_REQUEST"


def test_sort_on_missing_field_is_coded_error(execute, db):
    Tag.objects.create(label="x")
    result = execute("{ tagLists(sortBy: {field: CREATED_AT, direction: ASC}) { totalCount } }")
    assert result.errors[0].extensions["message_code"] == "INVALID_SORT_FIELD"
    assert "`CREATED_AT` is not available on Tag" in result.errors[0].message


def test_sort_on_missing_field_through_view(client, db):
    import json

    query = "{ tagLists(sortBy: {field: CREATED_AT, direction: ASC}) { totalCount } }"
    body = client.post(
        "/graphql/", json.dumps({"query": query}), content_type="application/json"
    ).json()
    assert body["message_code"] == "INVALID_SORT_FIELD"


def test_enforce_first_or_last(execute, books):
    result = execute("{ rawBookLists { totalCount } }")
    assert result.errors[0].extensions["message_code"] == "PAGINATION_REQUIRED"
    result = execute("{ rawBookLists(first: 2) { totalCount edges { node { name } } } }")
    assert result.errors is None
    assert len(result.data["rawBookLists"]["edges"]) == 2


def test_number_is_primary_key(execute, books):
    result = execute(LIST, {"first": 1, "sortBy": {"field": "NAME", "direction": "ASC"}})
    assert result.data["bookLists"]["edges"][0]["node"]["number"] == books[0].pk


@pytest.mark.parametrize("args", ['after: "garbage"', 'offset: 2, after: "garbage"', 'before: "x"'])
def test_invalid_cursor_is_bad_request(execute, books, args):
    result = execute(f"{{ bookLists({args}) {{ totalCount }} }}")
    assert result.errors[0].extensions["message_code"] == "BAD_REQUEST"
    assert "not a valid cursor" in result.errors[0].message


def test_first_zero_counts_as_given(execute, books):
    result = execute("{ rawBookLists(first: 0) { edges { node { name } } } }")
    assert result.errors is None
    assert result.data["rawBookLists"]["edges"] == []
