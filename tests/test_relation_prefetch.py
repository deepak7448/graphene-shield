"""graphene_shield.prefetch: relation prefetches that match graphene-django's loading."""

from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.db.models import Prefetch
from django.test.utils import CaptureQueriesContext

from graphene_shield.prefetch import (
    SelectionPrefetcher,
    nested,
    registered_type,
    relation_prefetch,
    under_path,
)
from tests.prefetch_schema import SEEN, AuthorType, schema
from tests.testapp.models import Article, ArticleStats, Author, Comment, Note

ARTICLE_FIELDS = "title author { name } editor { name } stats { views }"


def run(query):
    context = SimpleNamespace(META={}, headers={}, user=AnonymousUser())
    with CaptureQueriesContext(connection) as ctx:
        result = schema.execute(query, context_value=context)
    errors = sorted(
        # Drop the top-level field name so different fields can be compared.
        (e.message.replace("Plain", "").replace("Fast", ""), tuple(e.path[1:]))
        for e in result.errors or []
    )
    return result.data, errors, len(ctx.captured_queries)


def rows(data):
    """Rows of a plain list field or a connection field."""
    (value,) = data.values()
    if isinstance(value, dict):
        return [edge["node"] for edge in value["edges"]] if value else value
    return value


@pytest.fixture
def articles(db):
    authors = [Author.objects.create(name=f"A{i}") for i in range(3)]
    made = []
    for i in range(5):
        article = Article.objects.create(
            title=f"T{i}", author=authors[i % 3], editor=authors[(i + 1) % 3] if i % 2 else None
        )
        if i != 3:  # article 3 has no stats row
            ArticleStats.objects.create(article=article, views=i * 10)
        Comment.objects.create(article=article, text=f"c{i}")
        made.append(article)
    return made


def compare(query_name, fast_query_name, fields=ARTICLE_FIELDS):
    plain = run(f"{{ {query_name} {{ {fields} }} }}")
    fast = run(f"{{ {fast_query_name}(first: 10) {{ edges {{ node {{ {fields} }} }} }} }}")
    return plain, fast


# ── relation_resolver + relation_prefetch: same results as graphene-django ──


def test_prefetched_relations_match_default_resolvers(articles):
    (p_data, p_err, p_count), (f_data, f_err, f_count) = compare(
        "plainArticles", "fastArticleLists"
    )
    assert rows(p_data) == rows(f_data)
    assert p_err == f_err == []
    # Plain: 1 row query + a get_node per author, per editor, and a stats lookup per row.
    # Fast: 1 row query + one prefetch each for author, editor and stats.
    assert f_count == 4
    assert p_count > f_count


def same_as_plain(other_field, fields=ARTICLE_FIELDS):
    """Run plainArticles and another plain-list field; return both (rows, errors)."""
    p_data, p_err, _ = run(f"{{ plainArticles {{ {fields} }} }}")
    o_data, o_err, _ = run(f"{{ {other_field} {{ {fields} }} }}")
    return (rows(p_data), p_err), (rows(o_data), o_err)


def test_hidden_fk_target_errors_like_default_resolver(articles):
    # The non-null `author` FK points at a row its type's get_queryset hides:
    # graphene-django nulls the row with "Cannot return null"; so must we.
    Author.objects.filter(pk=articles[1].author_id).update(is_active=False)
    plain, prefetched = same_as_plain("prefetchedArticles")
    assert plain == prefetched
    assert plain[1] and "Cannot return null" in plain[1][0][0]


def test_hidden_nullable_fk_and_reverse_one_to_one_resolve_to_null(articles):
    Author.objects.filter(pk=articles[1].editor_id).update(is_active=False)
    ArticleStats.objects.filter(article=articles[0]).update(is_public=False)
    plain, prefetched = same_as_plain("prefetchedArticles", "title editor { name } stats { views }")
    assert plain == prefetched
    got, errors = prefetched
    assert errors == []
    assert got[0]["stats"] is None  # hidden by get_queryset
    assert got[3]["stats"] is None  # no row at all
    assert got[0]["editor"] is None  # null FK
    assert got[1]["editor"] is None  # hidden by get_queryset


@pytest.mark.parametrize("hide", ["author", "editor", "stats", "nothing"])
def test_resolvers_fall_back_without_a_prefetch(articles, hide):
    if hide == "author":
        Author.objects.filter(pk=articles[2].author_id).update(is_active=False)
    elif hide == "editor":
        Author.objects.filter(pk=articles[1].editor_id).update(is_active=False)
    elif hide == "stats":
        ArticleStats.objects.filter(article=articles[4]).update(is_public=False)
    plain, fallback = same_as_plain("fastArticles")
    assert plain == fallback


def test_only_selected_relations_are_prefetched(articles):
    _, _, count = run(
        "{ fastArticleLists(first: 10) { edges { node { title author { name } } } } }"
    )
    assert count == 2


def test_callable_builder_prefetch(articles):
    data, errors, count = run("{ fastArticleLists(first: 10) { edges { node { commentTexts } } } }")
    assert errors == []
    assert [r["commentTexts"] for r in rows(data)] == [[f"c{i}"] for i in range(5)]
    assert count == 2


def test_callable_builder_receives_the_sub_selection(articles):
    run("{ recordingArticleLists(first: 10) { edges { node { stats { views id } } } } }")
    assert SEEN["stats_subfields"] == ["id", "views"]


def test_nested_prefetcher_runs_under_the_relation(articles):
    fields = "text article { title author { name } stats { views } }"
    plain = run("{ plainComments { text plainArticle { title author { name } stats { views } } } }")
    fast = run(f"{{ fastCommentLists(first: 10) {{ edges {{ node {{ {fields} }} }} }} }}")
    assert [{"text": r["text"], "article": r["plainArticle"]} for r in rows(plain[0])] == rows(
        fast[0]
    )
    # comments + articles + authors + stats
    assert fast[2] == 4


def test_nested_prefetcher_skipped_when_relation_not_selected(articles):
    _, _, count = run("{ fastCommentLists(first: 10) { edges { node { text } } } }")
    assert count == 1


# ── building blocks ──


def test_registered_type():
    assert registered_type(Author) is AuthorType


def test_relation_prefetch_uses_the_registered_types_get_queryset(db):
    visible = Author.objects.create(name="shown")
    Author.objects.create(name="hidden", is_active=False)
    prefetch = relation_prefetch(Article, "author", info=None)
    assert prefetch.prefetch_through == "author"
    assert prefetch.to_attr == "prefetched_author"
    assert list(prefetch.queryset) == [visible]


def test_relation_prefetch_uses_base_manager_for_unguarded_types(db):
    # ContentType has no registered type, so graphene-django reads the attribute
    # (base manager); the prefetch must load it the same way.
    from django.contrib.contenttypes.models import ContentType

    prefetch = relation_prefetch(Note, "content_type", info=None)
    assert prefetch.queryset.model is ContentType
    assert prefetch.queryset.query.where.children == []


def test_relation_prefetch_through_a_path(db):
    prefetch = relation_prefetch(Article, "author", info=None, through="article")
    assert prefetch.prefetch_through == "article__author"
    assert prefetch.to_attr == "prefetched_author"


def test_relation_prefetch_rejects_to_many_relations():
    with pytest.raises(ValueError, match="one-to-one or many-to-one"):
        relation_prefetch(Article, "comments", info=None)


def test_under_path():
    assert under_path("article", "author") == "article__author"
    inner = Prefetch("author", queryset=Author.objects.all(), to_attr="x")
    moved = under_path("article", inner)
    assert (moved.prefetch_through, moved.to_attr, moved.queryset) == (
        "article__author",
        "x",
        inner.queryset,
    )


def test_selection_prefetcher_accepts_strings_prefetches_and_lists():
    prefetch = Prefetch("comments")
    prefetcher = SelectionPrefetcher(
        Article, {"a": "author", "b": prefetch, "c": ["editor", "stats"]}
    )
    node_fields = {"a": [None], "b": [None], "c": [None], "unknown": [None]}
    assert prefetcher.prefetches(node_fields, info=None) == ["author", prefetch, "editor", "stats"]


def test_nested_over_a_connection_reads_edges_node():
    inner = SelectionPrefetcher(Comment, {"text": "article"})
    builder = nested("comments", inner, connection=True)
    # `connection=True` reads row fields from `edges { node { ... } }`; with an
    # empty selection nothing nested is added, only the relation itself.
    assert builder.build(Article, [], info=None) == ["comments"]
