from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.db import models

from graphene_shield.relations import NonCascadingGenericRelation


class Note(models.Model):
    """Generic child row, attached to books (non-cascading) and tags (cascading)."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    text = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class Book(models.Model):
    name = models.CharField(max_length=100)
    author = models.CharField(max_length=100, blank=True)
    rating = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notes = NonCascadingGenericRelation(Note)

    class Meta:
        app_label = "testapp"


class Tag(models.Model):
    """Model without the BaseFilterInput common fields."""

    label = models.CharField(max_length=50)
    notes = GenericRelation(Note)

    class Meta:
        app_label = "testapp"


class LegacyItem(models.Model):
    """Model whose audit columns use non-standard names."""

    title = models.CharField(max_length=50)
    enabled = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "testapp"


class Review(models.Model):
    """Child rows for the prefetch hook tests."""

    book = models.ForeignKey(Book, related_name="reviews", on_delete=models.CASCADE)
    text = models.CharField(max_length=100)

    class Meta:
        app_label = "testapp"


class Author(models.Model):
    """Target of foreign keys; its node type hides inactive authors."""

    name = models.CharField(max_length=50)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "testapp"


class Article(models.Model):
    title = models.CharField(max_length=50)
    author = models.ForeignKey(Author, related_name="articles", on_delete=models.CASCADE)
    editor = models.ForeignKey(
        Author, null=True, blank=True, related_name="edited", on_delete=models.SET_NULL
    )

    class Meta:
        app_label = "testapp"


class ArticleStats(models.Model):
    """Reverse one-to-one target; its node type hides non-public stats."""

    article = models.OneToOneField(Article, related_name="stats", on_delete=models.CASCADE)
    views = models.IntegerField(default=0)
    is_public = models.BooleanField(default=True)

    class Meta:
        app_label = "testapp"


class Comment(models.Model):
    """Points at an article, for nested prefetches (comment -> article -> author)."""

    article = models.ForeignKey(Article, related_name="comments", on_delete=models.CASCADE)
    text = models.CharField(max_length=50)

    class Meta:
        app_label = "testapp"
