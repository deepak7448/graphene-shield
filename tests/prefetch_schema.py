"""Schema for tests/test_relation_prefetch.py.

``Plain*`` types use graphene-django's default relation resolvers and are the
reference behaviour; ``Fast*`` types use ``graphene_shield.prefetch``. Both must
return identical data and errors.
"""

import graphene
from django.db.models import Prefetch
from graphene import relay

from graphene_shield.connections import CountableDjangoObjectType
from graphene_shield.prefetch import (
    SelectionPrefetcher,
    nested,
    related,
    relation_prefetch,
    relation_resolver,
)
from graphene_shield.queries import ModelListField
from graphene_shield.selection import collect_selected_fields
from tests.testapp.models import Article, ArticleStats, Author, Comment


class AuthorType(CountableDjangoObjectType):
    class Meta:
        model = Author
        interfaces = (relay.Node,)
        fields = ("id", "name")

    @classmethod
    def get_queryset(cls, queryset, info):
        return queryset.filter(is_active=True)


class ArticleStatsType(CountableDjangoObjectType):
    class Meta:
        model = ArticleStats
        interfaces = (relay.Node,)
        fields = ("id", "views")

    @classmethod
    def get_queryset(cls, queryset, info):
        return queryset.filter(is_public=True)


class PlainArticleType(CountableDjangoObjectType):
    class Meta:
        model = Article
        interfaces = (relay.Node,)
        fields = ("id", "title", "author", "editor", "stats")
        skip_registry = True


def _comment_count(selection, info):
    """A callable builder: annotate-free prefetch of comments, only when selected."""
    return [
        Prefetch("comments", queryset=Comment.objects.order_by("pk"), to_attr="cached_comments")
    ]


ARTICLE_PREFETCHER = SelectionPrefetcher(
    Article,
    {
        "author": related("author"),
        "editor": related("editor"),
        "stats": related("stats"),
        "commentTexts": _comment_count,
    },
)


class FastArticleType(CountableDjangoObjectType):
    comment_texts = graphene.List(graphene.String)

    class Meta:
        model = Article
        interfaces = (relay.Node,)
        fields = ("id", "title", "author", "editor", "stats")

    prefetch_for_selection = ARTICLE_PREFETCHER
    resolve_author = relation_resolver("author")
    resolve_editor = relation_resolver("editor")
    resolve_stats = relation_resolver("stats")

    @staticmethod
    def resolve_comment_texts(root, info):
        comments = getattr(root, "cached_comments", None)
        if comments is None:
            comments = root.comments.order_by("pk")
        return [c.text for c in comments]


class PlainCommentType(CountableDjangoObjectType):
    plain_article = graphene.Field(PlainArticleType)

    class Meta:
        model = Comment
        interfaces = (relay.Node,)
        fields = ("id", "text")
        skip_registry = True

    @staticmethod
    def resolve_plain_article(root, info):
        return root.article


class FastCommentType(CountableDjangoObjectType):
    class Meta:
        model = Comment
        interfaces = (relay.Node,)
        fields = ("id", "text", "article")

    # comment -> article -> the article's own selected relations.
    prefetch_for_selection = SelectionPrefetcher(
        Comment, {"article": nested("article", ARTICLE_PREFETCHER)}
    )
    resolve_article = relation_resolver("article")


SEEN = {}


def _record(selection, info):
    SEEN["stats_subfields"] = sorted(collect_selected_fields(info, selection))
    return []


class RecordingArticleType(CountableDjangoObjectType):
    class Meta:
        model = Article
        interfaces = (relay.Node,)
        fields = ("id", "stats")
        skip_registry = True

    prefetch_for_selection = SelectionPrefetcher(Article, {"stats": _record})


class Query(graphene.ObjectType):
    plain_articles = graphene.List(PlainArticleType)
    fast_articles = graphene.List(FastArticleType)
    prefetched_articles = graphene.List(FastArticleType)
    fast_article_lists = ModelListField(FastArticleType)
    plain_comments = graphene.List(PlainCommentType)
    fast_comment_lists = ModelListField(FastCommentType)
    recording_article_lists = ModelListField(RecordingArticleType)

    @staticmethod
    def resolve_plain_articles(root, info):
        return Article.objects.select_related("author", "editor").order_by("pk")

    @staticmethod
    def resolve_fast_articles(root, info):
        # No prefetch: resolvers must fall back to graphene-django's per-row lookups.
        return Article.objects.order_by("pk")

    @staticmethod
    def resolve_prefetched_articles(root, info):
        # Same list shape as plainArticles, but with every relation prefetched.
        return Article.objects.order_by("pk").prefetch_related(
            *(relation_prefetch(Article, name, info) for name in ("author", "editor", "stats"))
        )

    @staticmethod
    def resolve_plain_comments(root, info):
        return Comment.objects.order_by("pk")


schema = graphene.Schema(query=Query)
