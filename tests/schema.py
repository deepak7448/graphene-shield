import graphene
from django.db.models import Prefetch
from graphene import relay
from graphql import GraphQLError

from graphene_shield.auth import jwt_required
from graphene_shield.authentication import login_required
from graphene_shield.connections import CountableDjangoObjectType, FilterConnectionField
from graphene_shield.filters import BaseFilterInput, IdFilterInput
from graphene_shield.messages import ErrorMessage
from graphene_shield.mutations import BaseMutation
from graphene_shield.queries import ModelListField
from graphene_shield.sorting import make_sort_field_enum, make_sorting_input
from tests.testapp.models import Book, LegacyItem, Review, Tag

NAME_REQUIRED = ErrorMessage("Name is required.", "NAME_REQUIRED")


class BookType(CountableDjangoObjectType):
    class Meta:
        model = Book
        interfaces = (relay.Node,)
        fields = ("id", "name", "author", "rating", "is_active")


class TagType(CountableDjangoObjectType):
    class Meta:
        model = Tag
        interfaces = (relay.Node,)
        fields = ("id", "label")


class LegacyItemType(CountableDjangoObjectType):
    class Meta:
        model = LegacyItem
        interfaces = (relay.Node,)
        fields = ("id", "title")


class ReviewedBookType(CountableDjangoObjectType):
    """Book type using the `prefetch_for_selection` hook for `reviewTexts`."""

    review_texts = graphene.List(graphene.String)

    class Meta:
        model = Book
        interfaces = (relay.Node,)
        fields = ("id", "name")
        skip_registry = True

    @classmethod
    def prefetch_for_selection(cls, queryset, node_fields, info):
        if "reviewTexts" in node_fields:
            reviews = Review.objects.order_by("pk")
            queryset = queryset.prefetch_related(
                Prefetch("reviews", queryset=reviews, to_attr="prefetched_reviews")
            )
        return queryset

    @staticmethod
    def resolve_review_texts(root, info):
        if hasattr(root, "prefetched_reviews"):
            reviews = root.prefetched_reviews
        else:  # one query per row: what the hook avoids
            reviews = root.reviews.order_by("pk")
        return [review.text for review in reviews]


class LegacyItemFilterInput(BaseFilterInput):
    common_field_map = {"is_active": "enabled", "created_at": "created"}


class BookFilterInput(BaseFilterInput):
    author = graphene.String()
    min_rating = graphene.Int()

    @classmethod
    def filter_queryset(cls, queryset, filters):
        if author := filters.get("author"):
            queryset = queryset.filter(author__iexact=author)
        if (min_rating := filters.get("min_rating")) is not None:
            queryset = queryset.filter(rating__gte=min_rating)
        return super().filter_queryset(queryset, filters)


BookSortField = make_sort_field_enum(
    "BookSortField", {"RATING": ["rating", "name"], "NAME": "name"}
)
BookSortingInput = make_sorting_input("BookSortingInput", BookSortField)


class Query(graphene.ObjectType):
    node = relay.Node.Field()
    book_lists = ModelListField(
        BookType,
        filter_input=BookFilterInput,
        search_fields=["name", "author"],
        sort_input=BookSortingInput,
        max_limit=5,
    )
    active_book_lists = ModelListField(BookType, filter_input=BookFilterInput)
    keyset_book_lists = ModelListField(
        BookType,
        filter_input=BookFilterInput,
        sort_input=BookSortingInput,
        pagination="keyset",
        max_limit=5,
    )
    keyset_by_date_lists = ModelListField(BookType, pagination="keyset")
    page_book_lists = ModelListField(
        BookType,
        filter_input=BookFilterInput,
        sort_input=BookSortingInput,
        pagination="page",
        max_limit=5,
    )
    tag_lists = ModelListField(TagType, filter_input=BaseFilterInput)
    id_only_tag_lists = ModelListField(TagType, filter_input=IdFilterInput, sort_input=None)
    legacy_item_lists = ModelListField(LegacyItemType, filter_input=LegacyItemFilterInput)
    raw_book_lists = FilterConnectionField(BookType, enforce_first_or_last=True)
    reviewed_book_lists = ModelListField(ReviewedBookType)
    keyset_reviewed_book_lists = ModelListField(ReviewedBookType, pagination="keyset")
    page_reviewed_book_lists = ModelListField(ReviewedBookType, pagination="page")
    union_reviewed_book_lists = FilterConnectionField(ReviewedBookType)
    whoami = graphene.String()
    me = graphene.String()
    boom = graphene.String()

    @staticmethod
    def resolve_active_book_lists(root, info, **kwargs):
        return Book.objects.filter(is_active=True)

    @staticmethod
    def resolve_union_reviewed_book_lists(root, info, **kwargs):
        # prefetch_related() is not allowed on a combined (.union()) queryset.
        return Book.objects.filter(rating__lt=3).union(Book.objects.filter(rating__gte=3))

    @staticmethod
    @jwt_required
    def resolve_whoami(root, info):
        return info.context.user.username

    @staticmethod
    @login_required
    def resolve_me(root, info):
        return info.context.user.username

    @staticmethod
    def resolve_boom(root, info):
        raise RuntimeError("database password is hunter2")


class CreateBook(BaseMutation):
    book = graphene.Field(BookType)

    class Arguments:
        name = graphene.String()
        author = graphene.String()

    @classmethod
    def validate_input(cls, info, data):
        if data.get("name") == "bad-validation":
            raise RuntimeError("validation crashed")
        errors = []
        cls._validate_required_fields(data, {"name": NAME_REQUIRED}, errors)
        return errors

    @classmethod
    def perform_mutation(cls, root, info, name, author=""):
        if name == "explode":
            raise RuntimeError("secret internal detail")
        if name == "write-then-explode":
            Book.objects.create(name=name)
            raise RuntimeError("failed after writing")
        if name == "forbidden":
            raise GraphQLError("Not allowed")
        if name == "forget":
            return None
        book = Book.objects.create(name=name, author=author)
        return cls(book=book, response=[cls.success("Book created.", "BOOK_CREATED")])


class GuardedMutation(BaseMutation):
    username = graphene.String()

    @classmethod
    @jwt_required
    def perform_mutation(cls, root, info):
        return cls(username=info.context.user.username, response=[cls.success("ok")])


class Signup(BaseMutation):
    login_required = True

    class Arguments:
        email = graphene.String()

    @classmethod
    def validate_input(cls, info, data):
        if data["email"] == "taken@x.com":
            return cls.failure("Email already registered", field="email")
        return None

    @classmethod
    def perform_mutation(cls, root, info, email):
        return cls(response=[cls.success("ok")])


class Mutation(graphene.ObjectType):
    create_book = CreateBook.Field()
    guarded = GuardedMutation.Field()
    signup = Signup.Field()


schema = graphene.Schema(query=Query, mutation=Mutation)
