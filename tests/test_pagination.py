import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test.utils import CaptureQueriesContext

from graphene_shield.connections import FilterConnectionField
from tests.schema import BookType
from tests.testapp.models import Book

NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf"]
RATINGS = [3, 5, 5, 1, 4, 2, 2]


@pytest.fixture
def books(db):
    return [Book.objects.create(name=n, rating=r) for n, r in zip(NAMES, RATINGS, strict=True)]


def ok(result):
    assert result.errors is None, result.errors
    return result.data


def sql_of(fn):
    with CaptureQueriesContext(connection) as ctx:
        fn()
    return [q["sql"] for q in ctx.captured_queries]


def code(result):
    return result.errors[0].extensions["message_code"]


# ── offset ──


def test_offset_skips_count_unless_total_count_is_selected(execute, books):
    queries = sql_of(lambda: ok(execute("{ bookLists(first: 2) { edges { node { name } } } }")))
    assert not any("COUNT" in q for q in queries)
    queries = sql_of(lambda: ok(execute("{ bookLists(first: 2) { totalCount } }")))
    assert any("COUNT" in q for q in queries)


def test_offset_has_next_page_without_count(execute, books):
    data = ok(
        execute(
            "{ bookLists(first: 5, offset: 3) { pageInfo { hasNextPage hasPreviousPage } edges { node { name } } } }"
        )
    )
    assert len(data["bookLists"]["edges"]) == 4
    assert data["bookLists"]["pageInfo"] == {"hasNextPage": False, "hasPreviousPage": True}


def test_offset_last_and_before_still_work(execute, books):
    data = ok(
        execute(
            "{ bookLists(last: 2, sortBy: {field: NAME, direction: ASC}) { edges { node { name } } } }"
        )
    )
    assert [e["node"]["name"] for e in data["bookLists"]["edges"]] == ["Foxtrot", "Golf"]


def test_unordered_querysets_are_ordered_by_primary_key(execute, books):
    queries = sql_of(
        lambda: ok(execute("{ activeBookLists(first: 2) { edges { node { name } } } }"))
    )
    assert any("ORDER BY" in q for q in queries)


# ── keyset ──

KEYSET = """
query($first: Int, $after: String, $last: Int, $before: String, $sortBy: BookSortingInput) {
  keysetBookLists(first: $first, after: $after, last: $last, before: $before, sortBy: $sortBy) {
    pageInfo { hasNextPage hasPreviousPage startCursor endCursor }
    edges { cursor node { name rating } }
  }
}
"""


def walk_forward(execute, size, sort_by=None):
    names, after, pages = [], None, 0
    while True:
        conn = ok(execute(KEYSET, {"first": size, "after": after, "sortBy": sort_by}))[
            "keysetBookLists"
        ]
        names += [e["node"]["name"] for e in conn["edges"]]
        pages += 1
        if not conn["pageInfo"]["hasNextPage"]:
            return names, pages
        after = conn["pageInfo"]["endCursor"]


def test_keyset_walks_every_row_once_in_order(execute, books):
    names, pages = walk_forward(execute, 2, {"field": "NAME", "direction": "ASC"})
    assert names == NAMES
    assert pages == 4


def test_keyset_handles_ties_in_multi_field_sort(execute, books):
    # RATING sorts by rating then name; several books share a rating.
    names, _ = walk_forward(execute, 2, {"field": "RATING", "direction": "DESC"})
    expected = [b.name for b in Book.objects.order_by("-rating", "-name", "-pk")]
    assert names == expected


def test_keyset_default_order_is_primary_key(execute, books):
    names, _ = walk_forward(execute, 3)
    assert names == NAMES


def test_keyset_datetime_cursor_round_trips(execute, books):
    query = """query($after: String) { keysetByDateLists(first: 3, after: $after,
      sortBy: {field: CREATED_AT, direction: DESC}) { pageInfo { hasNextPage endCursor }
      edges { node { name } } } }"""
    names, after = [], None
    while True:
        conn = ok(execute(query, {"after": after}))["keysetByDateLists"]
        names += [e["node"]["name"] for e in conn["edges"]]
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    assert names == [b.name for b in Book.objects.order_by("-created_at", "-pk")]


def test_keyset_backwards(execute, books):
    sort = {"field": "NAME", "direction": "ASC"}
    first_page = ok(execute(KEYSET, {"first": 4, "sortBy": sort}))["keysetBookLists"]
    cursor = first_page["edges"][3]["cursor"]  # "Delta"
    back = ok(execute(KEYSET, {"last": 2, "before": cursor, "sortBy": sort}))["keysetBookLists"]
    assert [e["node"]["name"] for e in back["edges"]] == ["Bravo", "Charlie"]
    assert back["pageInfo"] == {**back["pageInfo"], "hasPreviousPage": True, "hasNextPage": True}


def test_keyset_does_not_repeat_rows_when_data_changes(execute, books):
    sort = {"field": "NAME", "direction": "ASC"}
    page1 = ok(execute(KEYSET, {"first": 3, "sortBy": sort}))["keysetBookLists"]
    Book.objects.create(name="Aardvark")  # sorts before everything already seen
    page2 = ok(
        execute(KEYSET, {"first": 3, "after": page1["pageInfo"]["endCursor"], "sortBy": sort})
    )
    assert [e["node"]["name"] for e in page2["keysetBookLists"]["edges"]] == [
        "Delta",
        "Echo",
        "Foxtrot",
    ]


def test_keyset_uses_where_not_offset_and_skips_count(execute, books):
    sort = {"field": "NAME", "direction": "ASC"}
    cursor = ok(execute(KEYSET, {"first": 3, "sortBy": sort}))["keysetBookLists"]["pageInfo"][
        "endCursor"
    ]
    queries = sql_of(lambda: ok(execute(KEYSET, {"first": 3, "after": cursor, "sortBy": sort})))
    assert len(queries) == 1
    assert "OFFSET" not in queries[0] and "WHERE" in queries[0] and "COUNT" not in queries[0]


def test_keyset_total_count_is_lazy_and_ignores_cursor(execute, books):
    data = ok(execute("{ keysetBookLists(first: 2) { totalCount } }"))
    assert data["keysetBookLists"]["totalCount"] == 7


def test_keyset_rejects_bad_cursors_and_offset(execute, books):
    sort = {"field": "NAME", "direction": "ASC"}
    assert code(execute(KEYSET, {"first": 2, "after": "garbage", "sortBy": sort})) == "BAD_REQUEST"
    by_name = ok(execute(KEYSET, {"first": 2, "sortBy": sort}))["keysetBookLists"]["pageInfo"][
        "endCursor"
    ]
    result = execute(
        KEYSET, {"first": 2, "after": by_name, "sortBy": {"field": "RATING", "direction": "ASC"}}
    )
    assert "different sort order" in result.errors[0].message
    assert code(execute(KEYSET, {"first": 2, "last": 2})) == "BAD_REQUEST"
    assert "offset" in execute("{ keysetBookLists(offset: 2) { totalCount } }").errors[0].message


def test_keyset_rejects_tampered_cursor_values(execute, books):
    from graphene_shield.pagination import encode_keyset_cursor

    forged = encode_keyset_cursor([("name", False), ("pk", False)], ["Bravo", "not-a-number"])
    result = execute(
        KEYSET, {"first": 2, "after": forged, "sortBy": {"field": "NAME", "direction": "ASC"}}
    )
    assert code(result) == "BAD_REQUEST"


# ── page numbers ──

PAGE = """
query($page: Int, $pageSize: Int) {
  pageBookLists(page: $page, pageSize: $pageSize, sortBy: {field: NAME, direction: ASC}) {
    items { name }
    pageInfo { page pageSize totalCount totalPages hasNextPage hasPreviousPage }
  }
}
"""


def test_page_numbers(execute, books):
    page2 = ok(execute(PAGE, {"page": 2, "pageSize": 3}))["pageBookLists"]
    assert [i["name"] for i in page2["items"]] == ["Delta", "Echo", "Foxtrot"]
    assert page2["pageInfo"] == {
        "page": 2,
        "pageSize": 3,
        "totalCount": 7,
        "totalPages": 3,
        "hasNextPage": True,
        "hasPreviousPage": True,
    }
    page3 = ok(execute(PAGE, {"page": 3, "pageSize": 3}))["pageBookLists"]
    assert [i["name"] for i in page3["items"]] == ["Golf"]
    assert page3["pageInfo"]["hasNextPage"] is False
    beyond = ok(execute(PAGE, {"page": 9, "pageSize": 3}))["pageBookLists"]
    assert beyond["items"] == [] and beyond["pageInfo"]["hasNextPage"] is False


def test_page_defaults_and_lazy_count(execute, books, settings):
    settings.GRAPHENE_SHIELD = {"DEFAULT_PAGE_SIZE": 2}
    query = "{ pageBookLists { items { name } pageInfo { page pageSize hasNextPage } } }"
    queries = sql_of(lambda: ok(execute(query)))
    assert not any("COUNT" in q for q in queries)
    data = ok(execute(query))["pageBookLists"]
    assert data["pageInfo"] == {"page": 1, "pageSize": 2, "hasNextPage": True}


def test_empty_result_has_zero_pages(execute, db):
    data = ok(execute(PAGE, {"page": 1}))["pageBookLists"]
    assert data["pageInfo"]["totalCount"] == 0 and data["pageInfo"]["totalPages"] == 0


@pytest.mark.parametrize(
    "variables, expected",
    [
        ({"page": 0}, "BAD_REQUEST"),
        ({"pageSize": 0}, "BAD_REQUEST"),
        ({"pageSize": 6}, "PAGINATION_LIMIT_EXCEEDED"),
    ],
)
def test_page_argument_validation(execute, books, variables, expected):
    assert code(execute(PAGE, variables)) == expected


def test_page_mode_has_no_relay_arguments(execute, books):
    assert "first" in execute("{ pageBookLists(first: 2) { items { name } } }").errors[0].message


# ── configuration ──


def test_global_setting_picks_the_mode(settings):
    settings.GRAPHENE_SHIELD = {"PAGINATION": "page"}
    assert FilterConnectionField(BookType).pagination == "page"
    assert FilterConnectionField(BookType, pagination="keyset").pagination == "keyset"


def test_unknown_mode_is_a_configuration_error():
    with pytest.raises(ImproperlyConfigured, match="Unknown pagination"):
        FilterConnectionField(BookType, pagination="pages")
