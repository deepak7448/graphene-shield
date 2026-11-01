import pytest
from django.db.models import Prefetch

from graphene_shield.relations import NonCascadingGenericRelation
from tests.testapp.models import Book, Note, Tag


@pytest.fixture
def book_with_notes(db):
    book = Book.objects.create(name="Dune")
    Note.objects.create(target=book, text="first")
    Note.objects.create(target=book, text="second")
    return book


def test_plain_generic_relation_cascades(db):
    # The behaviour NonCascadingGenericRelation exists to avoid.
    tag = Tag.objects.create(label="scifi")
    Note.objects.create(target=tag, text="x")
    tag.delete()
    assert Note.objects.count() == 0


def test_deleting_the_parent_keeps_the_children(book_with_notes):
    book_with_notes.delete()
    assert sorted(Note.objects.values_list("text", flat=True)) == ["first", "second"]


def test_queryset_delete_keeps_the_children(book_with_notes):
    Book.objects.all().delete()
    assert Note.objects.count() == 2


def test_relation_can_be_queried(book_with_notes):
    assert sorted(book_with_notes.notes.values_list("text", flat=True)) == ["first", "second"]
    assert list(Book.objects.filter(notes__text="second")) == [book_with_notes]


def test_relation_can_be_prefetched(book_with_notes, django_assert_num_queries):
    other = Book.objects.create(name="Emma")
    Note.objects.create(target=other, text="third")
    with django_assert_num_queries(2):
        books = list(
            Book.objects.order_by("pk").prefetch_related(
                Prefetch("notes", queryset=Note.objects.order_by("pk"), to_attr="cached_notes")
            )
        )
        assert [[n.text for n in b.cached_notes] for b in books] == [["first", "second"], ["third"]]


def test_relation_adds_no_column():
    field = Book._meta.get_field("notes")
    assert isinstance(field, NonCascadingGenericRelation)
    assert field.column is None
    assert "notes" not in {f.name for f in Book._meta.concrete_fields}
