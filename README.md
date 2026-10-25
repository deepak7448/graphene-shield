# graphene-shield

[![PyPI](https://img.shields.io/pypi/v/graphene-shield.svg)](https://pypi.org/project/graphene-shield/)
[![CI](https://img.shields.io/github/actions/workflow/status/deepak7448/graphene-shield/ci.yml?branch=master&label=CI)](https://github.com/deepak7448/graphene-shield/actions)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/graphene-shield/)
[![Django](https://img.shields.io/badge/django-4.2%20%7C%205.2%20%7C%206.1-0C4B33)](https://pypi.org/project/graphene-shield/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Stop rewriting the same GraphQL plumbing in every Django project.** graphene-shield
adds the layer Graphene-Django leaves to you:

- one response shape for every mutation;
- filterable, sortable list fields with three pagination styles;
- upload validation that looks at file content, not file names;
- login checks that work with JWT, sessions or your own scheme;
- error responses that never leak internals.

Every part has a safe default.

```text
                          GraphQL request
                                 │
                                 ▼
                 ┌───────────────────────────────┐
                 │   GraphQLView (depth limit)   │
                 └───────────────┬───────────────┘
                                 │
               ┌─────────────────┴─────────────────┐
               ▼                                   ▼
          BaseMutation                       ModelListField
 login_required → sanitize input   filters → search → sort → paginate
  → validate_input (+ uploads)     (offset │ keyset │ page, lazy COUNT)
  → perform_mutation (atomic)                      │
               │                                   │
               └─────────────────┬─────────────────┘
                                 ▼
              data, or one JSON error body with a code
```

## Contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Why use it?](#why-use-it)
- [Mutations](#mutations)
- [List queries](#list-queries)
- [Pagination](#pagination)
- [File validation](#file-validation)
- [Authentication](#authentication)
- [Errors and query limits](#errors-and-query-limits)
- [Configuration](#configuration)
- [Limitations](#limitations)

More detail: [configuration reference](https://github.com/deepak7448/graphene-shield/blob/master/docs/configuration.md) ·
[API reference](https://github.com/deepak7448/graphene-shield/blob/master/docs/api.md) ·
[how it works](https://github.com/deepak7448/graphene-shield/blob/master/docs/architecture.md)

## Installation

```bash
pip install graphene-shield            # core
pip install "graphene-shield[all]"     # every optional extra
```

| Extra | Adds |
|---|---|
| `optimizer` | `graphene-django-optimizer`: list querysets are optimised automatically |
| `upload` | `graphene-file-upload`: `FileUploadGraphQLView` for multipart uploads |
| `files` | `pypdf` (thorough PDF check, recommended) and `python-magic` (legacy Office detection; needs the system `libmagic`) |
| `jwt` | `PyJWT`: the JWT authenticator |

Tested on Python 3.10–3.13 with Django 4.2, 5.2 and 6.1 (Django 6 needs Python 3.12+).

## Quick start

```python
# schema.py
import graphene
from graphene import relay
from graphene_shield.connections import CountableDjangoObjectType
from graphene_shield.mutations import BaseMutation
from graphene_shield.queries import ModelListField

from .models import Book


class BookType(CountableDjangoObjectType):
    class Meta:
        model = Book
        interfaces = (relay.Node,)
        fields = ("id", "title", "author")


class CreateBook(BaseMutation):
    book = graphene.Field(BookType)

    class Arguments:
        title = graphene.String(required=True)

    @classmethod
    def perform_mutation(cls, root, info, title):
        book = Book.objects.create(title=title)
        return cls(book=book, response=[cls.success("Book created.", "BOOK_CREATED")])


class Query(graphene.ObjectType):
    book_lists = ModelListField(BookType, search_fields=["title", "author"])


class Mutation(graphene.ObjectType):
    create_book = CreateBook.Field()


schema = graphene.Schema(query=Query, mutation=Mutation)
```

```python
# urls.py
from django.urls import path
from graphene_shield.views import GraphQLView

from .schema import schema

urlpatterns = [path("graphql/", GraphQLView.as_view(schema=schema))]
```

```graphql
mutation { createBook(title: "Dune") { book { title } response { message messageCode status } } }

{ bookLists(first: 10, search: "dune", sortBy: {field: CREATED_AT, direction: DESC}) {
    totalCount  edges { node { id title } } } }
```

## Why use it?

| Problem | Without the package | With it |
|---|---|---|
| Inconsistent errors | Each mutation reports errors its own way | One `response: [{message, messageCode, status, field}]` everywhere |
| Leaking internals | `return str(e)` sends SQL errors to clients | Errors are logged; clients get a generic message and a code |
| Half-finished writes | A crash after `create()` leaves partial data | Each mutation runs in a transaction |
| Copy-pasted list queries | Filters, search, sort and pagination rewritten per model | One `ModelListField(...)` |
| Unbounded queries | `first: 1000000` or deep nesting overloads the server | Page-size cap and query depth limit |
| Unsafe uploads | Trusting the file name | Content-based checks; dangerous PDFs and macro files rejected |
| Fragile JWT checks | Refresh tokens accepted, one generic "invalid token" | A specific code per failure; token type and revocation checked |
| Login checked too late | Validation leaks "email already registered" | `login_required` runs before validation |
| Silently ignored filters | A filter on a missing field returns *all* rows | A `BAD_REQUEST` error |

## Mutations

```python
from graphene_shield.messages import ErrorMessage
from graphene_shield.mutations import BaseMutation

TITLE_REQUIRED = ErrorMessage("Title is required.", "TITLE_REQUIRED")


class CreateTask(BaseMutation):
    login_required = True  # authenticate before anything else runs

    task = graphene.Field(TaskType)

    class Arguments:
        title = graphene.String()
        attachment = Upload()  # graphene_file_upload.scalars.Upload

    @classmethod
    def validate_input(cls, info, data):
        errors = []
        cls._validate_required_fields(data, {"title": TITLE_REQUIRED}, errors)
        if data.get("attachment"):
            cls._validate_file(data["attachment"], "attachment", errors, allowed_types={"PDF", "PNG"})
        return errors

    @classmethod
    def perform_mutation(cls, root, info, title, attachment=None):
        task = Task.objects.create(title=title)
        return cls(task=task, response=[cls.success("Task created.", "TASK_CREATED")])
```

A failed validation returns
`[{"message": "Title is required.", "messageCode": "TITLE_REQUIRED", "status": false, "field": "title"}]`.

- **Input clean-up.** String inputs are stripped first. Password, secret and token values are never touched.
- **Transactions.** `perform_mutation` runs in a transaction, so a failure rolls back its writes.
- **Unexpected exceptions** are logged, and the client gets `UNEXPECTED_ERROR`. Set `EXPOSE_EXCEPTION_MESSAGES = True` in development to see the real message.

## List queries

```python
from graphene_shield.authentication import login_required
from graphene_shield.filters import BaseFilterInput
from graphene_shield.queries import ModelListField
from graphene_shield.sorting import make_sort_field_enum, make_sorting_input


class TaskFilterInput(BaseFilterInput):  # adds id / isActive / createdAt / updatedAt
    status = graphene.String()

    @classmethod
    def filter_queryset(cls, queryset, filters):
        if status := filters.get("status"):
            queryset = queryset.filter(status=status)
        return super().filter_queryset(queryset, filters)


TaskSort = make_sort_field_enum("TaskSortField", {"DUE_DATE": ["due_date", "pk"]})


class Query(graphene.ObjectType):
    task_lists = ModelListField(
        TaskType,
        filter_input=TaskFilterInput,
        search_fields=["title", "assignee__email"],
        sort_input=make_sorting_input("TaskSortingInput", TaskSort),
        max_limit=100,
    )

    @login_required
    def resolve_task_lists(root, info, **kwargs):
        # Only scope what this user may see; filters, search, sort and paging are applied for you.
        return Task.objects.filter(school__members=info.context.user)
```

- **Models without audit columns.** For models without `is_active`/`created_at`/`updated_at`, use `IdFilterInput`, or rename columns with `common_field_map = {"is_active": "enabled"}`.
- **Unsupported filters or sorts** return `BAD_REQUEST` or `INVALID_SORT_FIELD`; rows are never silently left unfiltered.

## Pagination

Pick a style for every list with the `PAGINATION` setting, or per field with `pagination="..."`:

| | `"offset"` (default) | `"keyset"` | `"page"` |
|---|---|---|---|
| Client sends | `first`, `after`, `offset` | `first`, `after` | `page`, `pageSize` |
| Client gets | Relay connection | Relay connection | `items` + `pageInfo` |
| Deep pages | Slower the further you go | Fast | Slower the further you go |
| Rows added or removed while paging | Can skip or repeat rows | Never | Can skip or repeat rows |
| Best for | Admin tables | Infinite scroll, big tables | "Page 3 of 12" |

```graphql
{ auditLists(page: 3, pageSize: 20) {
    items { id }
    pageInfo { page totalPages totalCount hasNextPage } } }
```

In every style, `COUNT(*)` only runs when the client asks for `totalCount` or `totalPages`.

## File validation

```python
from graphene_shield.files import validate_file

error = validate_file(upload, allowed_types={"PDF", "DOCX"}, max_file_size=5 * 1024 * 1024)
# None if valid, otherwise FILE_REQUIRED / FILE_TOO_LARGE / INVALID_FILE_TYPE / ACTIVE_CONTENT_DETECTED
```

- **Detected from content.** The type comes from the file's bytes, not its name. Supported: PDF, PNG, JPEG, GIF, ICO, TXT/CSV, DOCX, XLSX, PPTX, DOC, XLS and PPT.
- **Always rejected:**
  - PDFs with JavaScript, auto-actions or embedded files (signed or not);
  - macro-enabled Office files;
  - HTML or SVG disguised as text;
  - files that can't be inspected.
- **Defaults:** 10 MB, PDF only.

## Authentication

`login_required = True` (mutations) and `@login_required` (queries) use the `AUTHENTICATOR` setting:

| Your project uses | `AUTHENTICATOR` |
|---|---|
| JWT (default) | `"graphene_shield.auth.authenticate_request"` |
| Django sessions | `"graphene_shield.authentication.session_authenticate"` |
| Anything else | Your own `fn(request) -> user` |

- **Header formats.** JWT accepts `Authorization: Bearer <token>` or `JWT <token>`, in any case. Other prefixes, bare tokens and custom headers are one setting away.
- **What's checked:** signature, expiry, algorithm, token type (so refresh tokens are rejected), revocation and inactive users. Each failure has its own error code.
- **Your own rules.** `USER_VALIDATOR = "myapp.auth.check_user"` adds app rules, such as requiring a verified email. It runs once per request with every authenticator.
- **No auth configured means public.** Without these checks, every list, `node(id:)` and mutation is open to everyone, so scope private data in resolvers and `get_queryset`.

## Errors and query limits

```python
# urls.py
from graphene_shield.views import FileUploadGraphQLView  # or GraphQLView

urlpatterns = [path("graphql/", FileUploadGraphQLView.as_view(graphiql=settings.DEBUG))]
```

Successful responses pass through unchanged. Errors come back as one body with HTTP 400:

```json
{"message": "Token has expired.", "message_code": "TOKEN_EXPIRED", "status": false,
 "field": null, "data": [], "errors": ["Token has expired."]}
```

Queries nested deeper than `MAX_QUERY_DEPTH` (15) are rejected before they run.

## Configuration

Every setting is optional. A misspelled key raises an error at startup.

```python
# settings.py
GRAPHENE_SHIELD = {
    "PAGINATION": "keyset",             # "offset" | "keyset" | "page"
    "CONNECTION_MAX_LIMIT": 100,        # largest page a client may request
    "EXPOSE_EXCEPTION_MESSAGES": DEBUG, # real error messages in development only
    "AUTHENTICATOR": "graphene_shield.authentication.session_authenticate",
    "USER_VALIDATOR": "accounts.auth.require_verified_email",
}
```

| Setting | Default | Purpose |
|---|---|---|
| `PAGINATION` | `"offset"` | Default pagination style |
| `CONNECTION_MAX_LIMIT` | `1000` | Largest `first`/`last`/`pageSize` |
| `DEFAULT_PAGE_SIZE` | `20` | Page size for `"page"` pagination |
| `MAX_QUERY_DEPTH` | `15` | Deepest allowed query (`None` disables) |
| `EXPOSE_EXCEPTION_MESSAGES` | `False` | Send real exception messages to clients |
| `AUTHENTICATOR` | JWT | How `login_required` authenticates |
| `USER_VALIDATOR` | `None` | Extra per-user login rules |
| `JWT_*` | See the reference | Signing key, algorithms, header prefixes, claims, revocation |

All settings, with seven ready-made setups (simplejwt, sessions, Auth0/Keycloak, mobile, admin dashboards…), are in the
[configuration reference](https://github.com/deepak7448/graphene-shield/blob/master/docs/configuration.md).

## Limitations

- **Graphene-Django only.** It isn't for Strawberry, Ariadne or REST APIs.
- **N+1 queries.** No prevention beyond the optional optimizer. Use `select_related`/`prefetch_related`.
- **Error envelope.** It returns one error body and drops partial `data`.
- **Query limits.** Only depth is limited; there are no alias, batch or cost limits.
- **Keyset pagination** can't jump to a page and needs non-null sort fields.
- **Upload checks.** Legacy Office macros and CSV formula injection aren't detected.
- **Features not included:** field-level permissions, async resolvers and subscriptions.

## License

MIT. See [LICENSE](https://github.com/deepak7448/graphene-shield/blob/master/LICENSE).
