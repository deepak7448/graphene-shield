"""The `prefetch_for_selection` hook and skipping row queries nobody reads."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from tests.testapp.models import Book, Review


@pytest.fixture
def reviewed_books(db):
    books = []
    for i in range(4):
        book = Book.objects.create(name=f"Book {i}", rating=i)
        Review.objects.create(book=book, text=f"r{i}a")
        Review.objects.create(book=book, text=f"r{i}b")
        books.append(book)
    return books


def run(execute, query):
    with CaptureQueriesContext(connection) as ctx:
        result = execute(query)
    assert result.errors is None, result.errors
    return result.data, [q["sql"] for q in ctx.captured_queries]


def review_queries(queries):
    return [q for q in queries if "testapp_review" in q]


def book_row_queries(queries):
    return [q for q in queries if "testapp_book" in q and "COUNT" not in q]


# ── prefetch_for_selection ──

CONNECTION_QUERIES = {
    "offset": "{ reviewedBookLists(first: 10) { edges { node { name reviewTexts } } } }",
    "keyset": "{ keysetReviewedBookLists(first: 10) { edges { node { name reviewTexts } } } }",
}


@pytest.mark.parametrize("mode", ["offset", "keyset"])
def test_hook_batches_selected_relation_in_connection_modes(execute, reviewed_books, mode):
    data, queries = run(execute, CONNECTION_QUERIES[mode])
    field = next(iter(data))
    texts = [edge["node"]["reviewTexts"] for edge in data[field]["edges"]]
    assert texts == [[f"r{i}a", f"r{i}b"] for i in range(4)]
    # One query for the page's reviews instead of one per book.
    assert len(review_queries(queries)) == 1


def test_hook_batches_selected_relation_in_page_mode(execute, reviewed_books):
    data, queries = run(execute, "{ pageReviewedBookLists(pageSize: 3) { items { reviewTexts } } }")
    texts = [item["reviewTexts"] for item in data["pageReviewedBookLists"]["items"]]
    assert texts == [[f"r{i}a", f"r{i}b"] for i in range(3)]
    assert len(review_queries(queries)) == 1


def test_hook_prefetches_only_the_page(execute, reviewed_books):
    data, queries = run(
        execute, "{ reviewedBookLists(first: 2, offset: 1) { edges { node { reviewTexts } } } }"
    )
    texts = [edge["node"]["reviewTexts"] for edge in data["reviewedBookLists"]["edges"]]
    assert texts == [["r1a", "r1b"], ["r2a", "r2b"]]
    (prefetch,) = review_queries(queries)
    # The IN (...) list holds the two books on the page, not all four.
    on_page = ", ".join(str(book.pk) for book in reviewed_books[1:3])
    assert f"IN ({on_page})" in prefetch


def test_unselected_relation_is_not_prefetched(execute, reviewed_books):
    _, queries = run(execute, "{ reviewedBookLists(first: 10) { edges { node { name } } } }")
    assert review_queries(queries) == []


def test_hook_is_skipped_for_combined_querysets(execute, reviewed_books):
    # prefetch_related() raises on .union() querysets; the field must not call it.
    data, _ = run(
        execute, "{ unionReviewedBookLists(first: 10) { edges { node { reviewTexts } } } }"
    )
    assert len(data["unionReviewedBookLists"]["edges"]) == 4


def test_total_count_is_unaffected_by_the_hook(execute, reviewed_books):
    data, _ = run(
        execute, "{ reviewedBookLists(first: 1) { totalCount edges { node { reviewTexts } } } }"
    )
    assert data["reviewedBookLists"]["totalCount"] == 4


# ── count-only selections don't fetch rows ──


@pytest.mark.parametrize(
    "query, field",
    [
        ("{ bookLists(first: 3) { totalCount } }", "bookLists"),
        ("{ keysetBookLists(first: 3) { totalCount } }", "keysetBookLists"),
        ("{ bookLists(last: 2) { totalCount } }", "bookLists"),
    ],
)
def test_count_only_connection_skips_row_query(execute, reviewed_books, query, field):
    data, queries = run(execute, query)
    assert data[field]["totalCount"] == 4
    assert book_row_queries(queries) == []
    assert len(queries) == 1  # just the COUNT


def test_count_only_page_mode_skips_row_query(execute, reviewed_books):
    data, queries = run(
        execute,
        "{ pageBookLists(pageSize: 3) { pageInfo { totalCount totalPages page pageSize hasPreviousPage } } }",
    )
    assert data["pageBookLists"]["pageInfo"] == {
        "totalCount": 4,
        "totalPages": 2,
        "page": 1,
        "pageSize": 3,
        "hasPreviousPage": False,
    }
    assert book_row_queries(queries) == []


def test_page_mode_has_next_page_still_fetches_rows(execute, reviewed_books):
    data, queries = run(execute, "{ pageBookLists(pageSize: 3) { pageInfo { hasNextPage } } }")
    assert data["pageBookLists"]["pageInfo"] == {"hasNextPage": True}
    assert len(book_row_queries(queries)) == 1


def test_page_info_still_fetches_rows_in_connection_mode(execute, reviewed_books):
    data, queries = run(execute, "{ bookLists(first: 3) { pageInfo { hasNextPage } } }")
    assert data["bookLists"]["pageInfo"] == {"hasNextPage": True}
    assert len(book_row_queries(queries)) == 1


def test_typename_only_runs_no_query(execute, reviewed_books):
    data, queries = run(execute, "{ bookLists(first: 3) { __typename } }")
    assert data["bookLists"]["__typename"] == "BookTypeCountableConnection"
    assert queries == []


def test_count_only_still_validates_keyset_arguments(execute, reviewed_books):
    result = execute("{ keysetBookLists(first: 1, last: 1) { totalCount } }")
    assert result.errors[0].extensions["message_code"] == "BAD_REQUEST"
    result = execute('{ keysetBookLists(first: 1, after: "garbage") { totalCount } }')
    assert result.errors[0].extensions["message_code"] == "BAD_REQUEST"
