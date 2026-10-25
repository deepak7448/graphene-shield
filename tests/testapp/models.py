from django.db import models


class Book(models.Model):
    name = models.CharField(max_length=100)
    author = models.CharField(max_length=100, blank=True)
    rating = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "testapp"


class Tag(models.Model):
    """Model without the BaseFilterInput common fields."""

    label = models.CharField(max_length=50)

    class Meta:
        app_label = "testapp"


class LegacyItem(models.Model):
    """Model whose audit columns use non-standard names."""

    title = models.CharField(max_length=50)
    enabled = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "testapp"
