"""Model fields that help list prefetching. Needs ``django.contrib.contenttypes``.

Import this module only from your models: importing it pulls in the content types
app, which must be in ``INSTALLED_APPS``.
"""

from __future__ import annotations

from django.contrib.contenttypes.fields import GenericRelation


class NonCascadingGenericRelation(GenericRelation):
    """A ``GenericRelation`` for querying and prefetching only.

    A plain ``GenericRelation`` makes Django's deletion collector delete every
    generic child when the parent is deleted. This one doesn't: deleting the
    parent leaves the related rows alone, exactly as if the field weren't there.
    Use it to add ``prefetch_related("notes")`` / ``filter(notes__...)`` support to
    an existing model without changing what a delete does.

    It has no column, so adding it needs no migration. Side effect: like any
    generic relation, it stops Django from using its fast ``DELETE`` path for the
    parent model (rows are loaded before deletion so signals still fire).
    """

    def bulk_related_objects(self, objs, using=None):
        # ``Collector.collect`` cascades through private fields via this method.
        return []
