# graphene-shield

[![PyPI](https://img.shields.io/pypi/v/graphene-shield.svg)](https://pypi.org/project/graphene-shield/)
[![CI](https://img.shields.io/github/actions/workflow/status/deepak7448/graphene-shield/ci.yml?branch=master&label=CI)](https://github.com/deepak7448/graphene-shield/actions)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/graphene-shield/)
[![Django](https://img.shields.io/badge/django-4.2%20%7C%205.2-0C4B33)](https://pypi.org/project/graphene-shield/)
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
- [Use cases](#use-cases)
- [How it works](#how-it-works)
- [Configuration](#configuration)
- [API reference](#api-reference)
- [Limitations](#limitations)

## Installation

```bash
pip install graphene-shield            # core
pip install "graphene-shield[all]"     # every optional extra
```

| Extra | Adds |
|---|---|
| `optimizer` | `graphene-django-optimizer`: connection querysets are optimised automatically |
| `upload` | `graphene-file-upload`: `FileUploadGraphQLView` |
| `files` | `pypdf`: the PDF active-content check parses the file's real objects (recommended). `python-magic`: legacy-Office detection (needs the system `libmagic`) |
| `jwt` | `PyJWT`: `graphene_shield.auth` |


The core needs only Django, graphene, graphene-django and graphql-relay. Python 3.10–3.13 and Django 4.2 or 5.2 are tested.

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
# → {"book": {"title": "Dune"}, "response": [{"message": "Book created.", "messageCode": "BOOK_CREATED", "status": true}]}

{ bookLists(first: 10, search: "dune", sortBy: {field: CREATED_AT, direction: DESC}) {
    totalCount  edges { node { id title } } } }
```

## Why use it?

Every Graphene-Django project ends up solving the same problems by hand. This package solves them once, with safe defaults.

| Problem | Without the package | With it |
|---|---|---|
| Inconsistent errors | Each mutation reports errors its own way; clients need special cases | Every mutation returns the same `response: [{message, messageCode, status, field}]` |
| Leaking internals | `return str(e)` sends SQL errors and paths to clients | Unexpected errors are logged; clients get a generic message and a code |
| Half-finished writes | A crash after `create()` leaves partial data behind | Each mutation runs in a transaction and rolls back on failure |
| Copy-pasted list queries | Filtering, search, sorting, pagination and counts rewritten per model | One `ModelListField(...)`; your resolver only scopes what the user may see |
| Unbounded queries | `first: 1000000` or deeply nested queries can take the server down | Page-size cap, validated pagination, query depth limit |
| Unsafe uploads | Trusting `.pdf` in the file name; renamed executables pass | Types detected from content; PDFs with JavaScript, macro-enabled Office files and disguised HTML are rejected |
| Fragile JWT checks | Expired tokens crash, refresh tokens work as access tokens, one generic "invalid token" | A specific error code for each failure; token type, revocation and inactive users checked |
| Login checked too late | Validation runs first and leaks "email already registered" to anonymous callers | `login_required = True` authenticates before anything else |
| Silently ignored filters | Filtering on a missing field returns *all* rows | `BAD_REQUEST` / `INVALID_SORT_FIELD` |

**Good fit:** Graphene-Django APIs with many models and admin-style list screens, and apps that accept document uploads (compliance, HR, education, insurance).

**Not a fit:** REST-only or Strawberry projects. Apps that rely on GraphQL's standard `errors` array (e.g. Apollo's default error handling) may prefer to keep it; the error envelope is optional (use your own view).

## Use cases

### 1. One response shape for every mutation

```python
import graphene
from graphene_shield.mutations import BaseMutation
from graphene_shield.messages import ErrorMessage
from graphene_shield.utils import resolve_global_id

TITLE_REQUIRED = ErrorMessage("Title is required.", "TITLE_REQUIRED")


class CreateTask(BaseMutation):
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

```graphql
mutation { createTask(title: "") { response { message messageCode status field } } }
# → [{"message": "Title is required.", "messageCode": "TITLE_REQUIRED", "status": false, "field": "title"}]
```

- Strings are stripped of surrounding whitespace before validation (`SANITIZE_INPUT`). Values under keys containing `password`, `secret` or `token` are never altered. HTML entities are kept as sent unless you set `UNESCAPE_HTML_INPUT = True`.
- Set `login_required = True` on a mutation to authenticate before anything else runs, so anonymous callers can't learn anything from validation errors (e.g. "email already registered").
- `perform_mutation` runs inside `transaction.atomic()` (`atomic = True`), so if it raises, everything it wrote is rolled back.
- An unexpected exception in `validate_input` or `perform_mutation` is logged, and the client receives `UNEXPECTED_ERROR` with a generic message. Set `EXPOSE_EXCEPTION_MESSAGES = True` (for example in development) to see `str(exc)` instead.
- A `GraphQLError` (including auth failures) propagates to the top-level `errors`.
- Returning `None` from `perform_mutation` raises an error instead of failing silently.

### 2. Filterable, searchable, sortable lists

```python
import graphene
from graphene import relay
from graphene_shield.authentication import login_required
from graphene_shield.connections import CountableDjangoObjectType
from graphene_shield.filters import BaseFilterInput
from graphene_shield.queries import ModelListField
from graphene_shield.sorting import make_sort_field_enum, make_sorting_input


class TaskType(CountableDjangoObjectType):
    class Meta:
        model = Task
        interfaces = (relay.Node,)
        fields = "__all__"


class TaskFilterInput(BaseFilterInput):           # adds id / isActive / createdAt / updatedAt
    status = graphene.String()

    @classmethod
    def filter_queryset(cls, queryset, filters):
        if status := filters.get("status"):
            queryset = queryset.filter(status=status)
        return super().filter_queryset(queryset, filters)


TaskSortField = make_sort_field_enum("TaskSortField", {
    "CREATED_AT": ["created_at", "pk"],     # every field is used, so ordering is stable
    "DUE_DATE": ["due_date", "pk"],
})


class Query(graphene.ObjectType):
    task_lists = ModelListField(
        TaskType,
        filter_input=TaskFilterInput,
        search_fields=["title", "description", "assignee__email"],
        sort_input=make_sorting_input("TaskSortingInput", TaskSortField),
        max_limit=100,
    )

    @login_required
    def resolve_task_lists(root, info, **kwargs):
        # Only scope the base queryset; filters/search/sort/pagination are applied for you.
        return Task.objects.filter(school__members=info.context.user)
```

```graphql
{
  taskLists(first: 20, offset: 40, search: "fire", filters: {status: "OPEN"},
            sortBy: {field: DUE_DATE, direction: ASC}) {
    totalCount
    pageInfo { hasNextPage }
    edges { node { id number title } }
  }
}
```

`FilterConnectionField` is the lower-level field without the filter, search and sort arguments. It still gives you the pagination styles below, `max_limit` (default `CONNECTION_MAX_LIMIT`) and `enforce_first_or_last`. Breaking a pagination limit returns a coded GraphQL error (`PAGINATION_LIMIT_EXCEEDED`, `PAGINATION_REQUIRED`...); it is not an assertion.

`QuerySetProcessor` and `resolve_model_list()` are also available if you want to filter, search and sort a queryset yourself.

### 3. Choosing a pagination style

Choose the style globally with the `PAGINATION` setting, or per field with `pagination=...`:

```python
GRAPHENE_SHIELD = {"PAGINATION": "keyset"}              # default for every list field

audit_lists = ModelListField(AuditType, pagination="page")   # this field only
```

| | `"offset"` (default) | `"keyset"` | `"page"` |
|---|---|---|---|
| Client arguments | `first`, `after`, `last`, `before`, `offset` | `first`, `after`, `last`, `before` | `page`, `pageSize` |
| Response | Relay connection | Relay connection (same shape) | `items` + `pageInfo` |
| SQL | `LIMIT … OFFSET …` | `WHERE (sort columns) > (cursor values) LIMIT …` | `LIMIT … OFFSET …` |
| Jump to any position/page | Yes (`offset`) | No, only next/previous | Yes (`page`) |
| Fast on deep pages | No, the database skips every earlier row | Yes | No |
| Rows inserted/deleted while paging | Can skip or repeat rows | Never skips or repeats | Can skip or repeat rows |
| Best for | Admin tables, existing Relay clients | Infinite scroll, feeds, large tables, exports | Numbered pages: "Page 3 of 12" |

```graphql
# keyset: same as offset for the client, minus `offset`
{ auditLists(first: 20, after: "<endCursor>") {
    pageInfo { hasNextPage endCursor } edges { node { id } } } }

# page
{ auditLists(page: 3, pageSize: 20) {
    items { id }
    pageInfo { page pageSize totalCount totalPages hasNextPage hasPreviousPage } } }
```

Details:

- **`COUNT(*)` is lazy in every mode.** It only runs when the client selects `totalCount` or `totalPages`. Has-next is found by fetching one extra row.
- **Stable order.** An unordered queryset is ordered by primary key, so pages never overlap. Keyset always adds the primary key as a final tie-breaker.
- **Keyset rules.**
  - It follows the queryset's ordering: `sortBy`, an `order_by(...)` in your resolver, or the model's `Meta.ordering`. That ordering must use plain field names.
  - A sort field that is empty (`NULL`) can't be paged past, so sort on non-null fields.
  - Each cursor records the sort order it was made for. A cursor reused with a different `sortBy` gets a `BAD_REQUEST`, and so do tampered cursors.
  - `first` and `last` can't be combined.
- **Page mode.** `pageSize` defaults to `DEFAULT_PAGE_SIZE` (20) and is capped by `max_limit`. The type is named `<Node>Page`, for example `AuditTypePage`.

### 4. Models without `is_active` / `created_at` / `updated_at`

`BaseFilterInput` assumes those columns. `id` always works, because it matches the primary key whatever its name or type. For the other three:

| Situation | What to use |
|---|---|
| The model has none of these columns | `IdFilterInput`, which exposes only `id` |
| The columns exist under other names | `common_field_map = {"is_active": "enabled", "created_at": "created"}` on your filter input |
| A client filters on a column the model lacks | A `BAD_REQUEST` error (``Filter `isActive` is not supported for Tag.``), never unfiltered rows |

The same goes for sorting. The default `SortingInput` offers `ID`, `CREATED_AT`, `UPDATED_AT` and `NAME`. For models without those fields, pass your own `make_sorting_input(...)`, or use `sort_input=None`. Sorting on a field the model lacks returns `INVALID_SORT_FIELD`.

`FilterConnectionField` is the lower-level field without the filter, search and sort arguments. It still gives you the pagination modes below, `max_limit` (default `CONNECTION_MAX_LIMIT`), and `enforce_first_or_last`. Breaking a pagination limit returns a coded GraphQL error (`PAGINATION_LIMIT_EXCEEDED`, `PAGINATION_REQUIRED`...); it is not an assertion.

`QuerySetProcessor` and `resolve_model_list()` are also available if you want to filter, search and sort a queryset yourself.

### 5. Validating uploads

```python
from graphene_shield.files import detect_file_type, validate_file

detect_file_type(upload)            # ("PDF", "application/pdf") | (None, mime)
validate_file(upload, allowed_types={"PDF", "DOCX"}, max_file_size=5 * 1024 * 1024)
# → None if valid, else FileErrorMessages.FILE_REQUIRED / FILE_TOO_LARGE / INVALID_FILE_TYPE / ACTIVE_CONTENT_DETECTED
```

Defaults: 10 MB (`files.DEFAULT_MAX_FILE_SIZE`) and PDF only (`files.DEFAULT_ALLOWED_TYPES`). Pass `max_file_size` / `allowed_types` per call to change them.

Recognised types: PDF, PNG, JPEG, GIF, ICO, TXT/CSV, DOCX, XLSX, PPTX, DOC, XLS and PPT. DOC, XLS and PPT need the `files` extra. A file whose header claims one format but whose structure does not match is rejected. These are always rejected:

- macro-enabled Office files (`.docm`, `.xlsm`, `.pptm`);
- text files that contain HTML, SVG or XML markup, since a browser could run them;
- PDFs containing `/JavaScript`, `/JS`, `/OpenAction`, `/AA`, `/Launch`, `/SubmitForm`, `/ImportData`, `/GoToR`, `/GoToE`, `/RichMedia`, `/XFA` or embedded files. Digital signatures are not checked, so a signed PDF with active content is rejected too.

With `pypdf` installed (the `files` extra), the PDF check walks every object a PDF reader would use, including compressed object streams and `#`-escaped names. Without it, a byte-level scan of the file's dictionaries runs instead, which is a heuristic. Either way, files that can't be inspected (unparseable, password-protected, unreadable object streams, compression bombs) are rejected.

Not covered: macros in legacy `.doc`/`.xls`/`.ppt` files, and CSV formula injection (`=HYPERLINK(...)` cells). Handle those where the files are opened or exported.

### 6. Login with JWT, sessions or your own scheme

`BaseMutation.login_required = True` and the `@login_required` resolver decorator call the function named by the `AUTHENTICATOR` setting. It returns the user, or raises `EnhancedGraphQLError` to reject the request. Pick one of these:

| Your project uses | `AUTHENTICATOR` |
|---|---|
| JWT in a header (default) | `"graphene_shield.auth.authenticate_request"` (needs the `jwt` extra) |
| Django sessions, or any middleware that sets `request.user` | `"graphene_shield.authentication.session_authenticate"` (no PyJWT needed) |
| API keys, cookies, an external identity provider... | Your own `"myapp.auth.authenticate"`, i.e. `fn(request) -> user` |

```python
from graphene_shield.authentication import login_required

class Query(graphene.ObjectType):
    me = graphene.Field(UserType)

    @login_required
    def resolve_me(root, info):
        return info.context.user

class UpdateProfile(BaseMutation):
    login_required = True   # authenticates before validate_input runs

    @classmethod
    def perform_mutation(cls, root, info, **data): ...
```

`@jwt_required` (from `graphene_shield.auth`) always uses JWT, whatever `AUTHENTICATOR` is set to.

JWT header formats:

| Client sends | Accepted by default? | To change |
|---|---|---|
| `Authorization: Bearer <token>` | Yes, any case (`bearer`, `BEARER`) | |
| `Authorization: JWT <token>` (django-graphql-jwt style) | Yes, any case | |
| `Authorization: Token <token>` | No | `"JWT_AUTH_HEADER_PREFIX": ["Bearer", "JWT", "Token"]` |
| `Authorization: <token>` (no prefix) | No | add `""` to `JWT_AUTH_HEADER_PREFIX` |
| `X-Auth-Token: Bearer <token>` | No | `"JWT_AUTH_HEADER_NAME": "HTTP_X_AUTH_TOKEN"` |

`JWT_AUTH_HEADER_PREFIX` takes a string or a list. Setting it replaces the defaults, so include every prefix your clients send.

The JWT authenticator verifies the token's signature, `exp`, algorithm and (optionally) audience and issuer. It rejects tokens whose `jti` is in the cache under `JWT_BLACKLIST_CACHE_PREFIX`, and tokens whose `token_type` claim isn't `access` (so a refresh token can't be used as an access token). Then it loads the user, rejects inactive accounts, and sets `request.user` and `request.jwt_payload`.

Error codes: `PERMISSION_DENIED`, `INVALID_FORMAT`, `TOKEN_EXPIRED`, `TOKEN_REVOKED`, `WRONG_TOKEN_TYPE`, `INVALID_SIGNATURE`, `INVALID_ALGORITHM`, `INVALID_AUDIENCE`, `INVALID_ISSUER`, `TOKEN_INVALID`, `PAYLOAD_INVALID`, `USER_NOT_FOUND` and `USER_INACTIVE`.

### 7. Your own login rules

After any authenticator accepts the user, `USER_VALIDATOR(user, payload, request)` runs once. `payload` is the decoded JWT for the JWT authenticator and `None` for the others. Raise `EnhancedGraphQLError` to reject the request:

```python
def check_user(user, payload, request):
    if not user.profile.email_verified:
        raise EnhancedGraphQLError("Please verify your email first.",
                                   message_code="EMAIL_NOT_VERIFIED", status=False)

GRAPHENE_SHIELD = {"USER_VALIDATOR": "myapp.auth.check_user"}
```

Typical rules: email verified, account or organisation suspended, a password change logging out old tokens (`payload["iat"]` before `user.password_changed_at`), role changed since login. It runs on every authenticated request, so keep it cheap. Raise `EnhancedGraphQLError` rather than other exceptions, which are treated as server errors. A rejected user is never left on `request.user`.

### 8. Public APIs without authentication

Authentication is opt-in. Without `login_required`, `@login_required` or `@jwt_required`, nothing is checked and PyJWT isn't needed. That also means **everything is public**:

- `ModelListField` without a resolver returns every row of the model's default manager, including inactive or soft-deleted rows.
- `node(id: ...)` returns any object by ID.
- Every mutation can be called by anyone.

If some data is private, scope it explicitly: return a filtered queryset from the list resolver, and override `get_queryset` on the type (it is also used by `node`). If authentication is misconfigured (e.g. `login_required = True` but PyJWT missing), requests are rejected and the cause is logged; nothing is let through.

### 9. A predictable error body and query limits

```python
# urls.py
from django.views.decorators.csrf import csrf_exempt
from graphene_shield.views import FileUploadGraphQLView  # or GraphQLView

urlpatterns = [path("graphql/", csrf_exempt(FileUploadGraphQLView.as_view(graphiql=settings.DEBUG)))]
```

Successful responses are left unchanged. When there are errors, the body becomes the following, returned with HTTP 400:

```json
{"message": "Token has expired.", "message_code": "TOKEN_EXPIRED", "status": false,
 "field": null, "data": [], "errors": ["Token has expired."]}
```

Unexpected resolver exceptions are logged, and their messages are masked unless `EXPOSE_EXCEPTION_MESSAGES` is on.

Operations nested deeper than `MAX_QUERY_DEPTH` (default 15; introspection is exempt) are rejected before they run. The depth limit doesn't stop wide queries that repeat a field under many aliases; use rate limiting or query-cost analysis for that.

## How it works

- **Mutation lifecycle.** `BaseMutation.mutate` does these steps in order:
  1. Authenticates, if `login_required` is set.
  2. Strips string inputs, skipping password/secret/token keys.
  3. Runs `validate_input`.
  4. Runs `perform_mutation` inside `transaction.atomic()`.

  A `GraphQLError` passes straight through to the top-level `errors`. Any other exception is logged, rolled back and returned as `UNEXPECTED_ERROR`.
- **Lists.** The resolver returns a base queryset (or nothing, meaning the default manager). Then the type's `get_queryset` runs, followed by the optional query optimizer, then filters → search (`icontains` ORed across `search_fields`) → sort → pagination.
- **Pagination SQL.**
  - `offset` and `page` use `LIMIT/OFFSET`.
  - `keyset` adds a `WHERE (a > x) OR (a = x AND b > y) …` condition built from the ordering, with the primary key always last.
  - Every style fetches one extra row to answer `hasNextPage`, so `COUNT(*)` only runs for `totalCount`/`totalPages`.
  - Keyset cursors are base64 JSON holding the sort order and the row's values, at full datetime precision. They are checked on every use.
- **File types.** Types are decided from magic bytes and container structure: PDF structure markers, the OOXML parts inside a zip, OLE headers. libmagic is only consulted when that can't decide.
- **PDF active content.** Two layers check every upload:
  - With `pypdf`, every object reachable from the trailer is walked, including compressed object streams.
  - A byte scan checks the text outside stream bodies, after decoding `#xx` name escapes, and inflates object streams within a 64 MB budget.

  Anything that can't be inspected is rejected.
- **Authentication.** `authenticate()` runs the configured authenticator, then `USER_VALIDATOR`, and remembers the outcome on the request. Every guarded field after the first costs nothing. A rejection restores the previous `request.user`.
- **Errors.** The views check every response for an error key. Responses without errors are passed through unchanged. Responses with errors are re-encoded as one `ResponseField`-shaped body; exceptions that escaped a resolver are logged, and their messages masked.

## Configuration

All settings live in one dict in your Django settings, and **every key is optional**. Leave out what you don't need and the defaults below apply.

```python
# settings.py
GRAPHENE_SHIELD = {
    "PAGINATION": "keyset",
    "EXPOSE_EXCEPTION_MESSAGES": DEBUG,
}
```

- **Typos are errors.** An unknown key raises `ImproperlyConfigured` as soon as settings are first read (usually at startup, when the schema is built), naming the key (and the new name if it was renamed). A misspelled setting can't silently do nothing.
- **Tests can override settings.** `override_settings(GRAPHENE_SHIELD={...})` and pytest-django's `settings` fixture take effect immediately.
- **Dotted paths or callables.** `AUTHENTICATOR` and `USER_VALIDATOR` accept either.
- **`PAGINATION` is read when a field is defined**, i.e. when the schema is built, because it decides the field's arguments and type. All other settings are read when they're used.

### Recipes

**1. Defaults: nothing to configure.** You get offset pagination capped at 1,000 rows, a query depth limit of 15, masked internal errors, and JWT login (`Bearer` or `JWT` prefix) when you use `login_required`.

**2. JWT from `djangorestframework-simplejwt`**

```python
GRAPHENE_SHIELD = {
    # simplejwt signs with SECRET_KEY/HS256 and uses these claim names by default.
    "JWT_USER_ID_CLAIM": "user_id",
    "JWT_TOKEN_TYPE_CLAIM": "token_type",       # refresh tokens are rejected
    "JWT_BLACKLIST_CACHE_PREFIX": "blacklist:",  # if logout does cache.set(f"blacklist:{jti}", 1)
    "USER_VALIDATOR": "accounts.auth.require_verified_email",
    "EXPOSE_EXCEPTION_MESSAGES": DEBUG,
}
```

**3. A classic Django site with session login (no JWT, no PyJWT needed)**

```python
GRAPHENE_SHIELD = {
    "AUTHENTICATOR": "graphene_shield.authentication.session_authenticate",
}
```

Keep Django's CSRF protection on the GraphQL view (don't wrap it in `csrf_exempt`) when using cookies.

**4. Tokens from an identity provider (Auth0, Keycloak, Cognito...) signed with RS256**

```python
GRAPHENE_SHIELD = {
    "JWT_SIGNING_KEY": open(BASE_DIR / "idp_public_key.pem").read(),  # the PUBLIC key
    "JWT_ALGORITHMS": ["RS256"],
    "JWT_AUDIENCE": "https://api.example.com",
    "JWT_ISSUER": "https://example.eu.auth0.com/",
    "JWT_USER_ID_CLAIM": "sub",          # the provider's user id...
    "JWT_USER_ID_FIELD": "username",     # ...stored in User.username
    "JWT_TOKEN_TYPE_CLAIM": None,        # these tokens have no token_type claim
    "JWT_BLACKLIST_CACHE_PREFIX": None,  # revocation is handled by the provider
    "JWT_LEEWAY": 30,                    # tolerate 30 s of clock difference
}
```

**5. A mobile app with infinite scroll and big tables**

```python
GRAPHENE_SHIELD = {
    "PAGINATION": "keyset",        # fast deep pages, no skipped rows while scrolling
    "CONNECTION_MAX_LIMIT": 100,   # nobody needs 1,000 rows on a phone
    "MAX_QUERY_DEPTH": 10,
}
```

**6. An admin dashboard with numbered pages**

```python
GRAPHENE_SHIELD = {
    "PAGINATION": "page",
    "DEFAULT_PAGE_SIZE": 25,
    "CONNECTION_MAX_LIMIT": 200,   # the largest pageSize a client may ask for
}
```

**7. Clients that send tokens differently**

```python
GRAPHENE_SHIELD = {
    "JWT_AUTH_HEADER_NAME": "HTTP_X_AUTH_TOKEN",            # header "X-Auth-Token"
    "JWT_AUTH_HEADER_PREFIX": ["Bearer", "JWT", "Token", ""],  # "" = bare token, no prefix
}
```

### Reference

#### Errors and mutations

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `EXPOSE_EXCEPTION_MESSAGES` | `False` | When an unexpected exception happens in a mutation or resolver, clients see `UNEXPECTED_ERROR_MESSAGE`; the real error is logged. `True` sends `str(exc)` instead. | Development only, e.g. `DEBUG` |
| `UNEXPECTED_ERROR_MESSAGE` | `"Something went wrong. Please try again later."` | The message used instead of a hidden exception. | You want different wording or a translation key |
| `UNEXPECTED_ERROR_CODE` | `"UNEXPECTED_ERROR"` | `messageCode` of that failed mutation response. | Your clients expect another code |
| `SANITIZE_INPUT` | `True` | Strips whitespace around every string argument before `validate_input`. Values under keys containing `password`, `secret` or `token` are never touched. | You need leading/trailing spaces kept everywhere |
| `UNESCAPE_HTML_INPUT` | `False` | Also turns `&amp;` into `&` (repeatedly) in string arguments. Off because it undoes escaping a client or proxy did on purpose. | A legacy client double-escapes text |

#### Lists and pagination

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `PAGINATION` | `"offset"` | Default style for `ModelListField`/`FilterConnectionField`: `"offset"`, `"keyset"` or `"page"` (see [Choosing a pagination style](#3-choosing-a-pagination-style)). A field's `pagination=` argument overrides it. | You want keyset or page numbers everywhere |
| `CONNECTION_MAX_LIMIT` | `1000` | Largest `first`/`last`/`pageSize` a client may request, and the page size of a connection when the client gives none. A field's `max_limit=` overrides it. `None` removes the cap; don't do that on a public API. | Lower it for mobile/public APIs |
| `DEFAULT_PAGE_SIZE` | `20` | `pageSize` when a `"page"` field is called without one (never above `max_limit`). | Your UI shows a different number of rows |

#### Query limits

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `MAX_QUERY_DEPTH` | `15` | Shield views reject operations nested deeper than this before running them. Introspection is exempt. `None` disables it. | Your schema legitimately nests deeper, or you want it stricter |

#### Authentication

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `AUTHENTICATOR` | `"graphene_shield.auth.authenticate_request"` (JWT) | `fn(request) -> user` used by `login_required = True` and `@login_required`. Raise `EnhancedGraphQLError` to reject. The result is cached for the rest of the request. | Sessions (`...authentication.session_authenticate`), API keys, cookies, SSO |
| `USER_VALIDATOR` | `None` | `fn(user, payload, request)` run once per request after any authenticator succeeds. `payload` is the JWT claims, or `None`. Raise to reject. | App rules: email verified, account suspended, password changed |

#### JWT (only used by the JWT authenticator and `@jwt_required`)

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `JWT_SIGNING_KEY` | `None` → `SECRET_KEY` | Key used to verify signatures. For RS256/ES256, the **public** key. | Tokens come from another service |
| `JWT_ALGORITHMS` | `["HS256"]` | Allowed algorithms. Any other is rejected (`INVALID_ALGORITHM`). | RS256/ES256 tokens |
| `JWT_AUDIENCE` | `None` | If set, the `aud` claim must match (`INVALID_AUDIENCE`). | One identity provider serves several APIs |
| `JWT_ISSUER` | `None` | If set, the `iss` claim must match (`INVALID_ISSUER`). | Tokens from an identity provider |
| `JWT_LEEWAY` | `0` | Seconds of clock difference tolerated for `exp`/`nbf`/`iat`. | Servers' clocks drift |
| `JWT_AUTH_HEADER_NAME` | `"HTTP_AUTHORIZATION"` | `request.META` key holding the token. `"HTTP_X_AUTH_TOKEN"` means header `X-Auth-Token`. | Clients use a custom header |
| `JWT_AUTH_HEADER_PREFIX` | `["Bearer", "JWT"]` | Accepted prefixes, any case. A string or a list; your list **replaces** the default. Add `""` to accept a bare token. | Clients send `Token ...` or no prefix |
| `JWT_USER_ID_CLAIM` | `"user_id"` | Claim holding the user's id. | Provider tokens use `sub` |
| `JWT_USER_ID_FIELD` | `"pk"` | User model field that claim is matched against. | Ids are usernames, emails or UUIDs |
| `JWT_TOKEN_TYPE_CLAIM` | `"token_type"` | If a token has this claim, its value must be in `JWT_ALLOWED_TOKEN_TYPES` (`WRONG_TOKEN_TYPE`). `None` disables it. | Your tokens use another claim name |
| `JWT_ALLOWED_TOKEN_TYPES` | `["access"]` | Accepted values of that claim. | Your access tokens are labelled differently |
| `JWT_BLACKLIST_CACHE_PREFIX` | `"blacklist:"` | A token whose `jti` is in the cache under this prefix is rejected (`TOKEN_REVOKED`). `None` disables it. | You revoke tokens another way |

#### Not settings: per-call arguments

File validation limits are passed per upload, because different uploads need different rules:

```python
validate_file(upload, allowed_types={"PDF", "PNG"}, max_file_size=5 * 1024 * 1024)
# defaults: files.DEFAULT_ALLOWED_TYPES ({"PDF"}) and files.DEFAULT_MAX_FILE_SIZE (10 MB)
```

## API reference

### Mutations: `graphene_shield.mutations`

`BaseMutation`. Subclass it and add `class Arguments` and output fields as usual. Every subclass gets a `response: [ResponseField]` field.

| Member | Description |
|---|---|
| `login_required = False` | Authenticate with `AUTHENTICATOR` before anything else runs. |
| `atomic = True` | Run `perform_mutation` in `transaction.atomic()`. |
| `validate_input(info, data)` | Return a `ResponseField` or a list of them to stop with errors; `None`/`[]` to continue. |
| `perform_mutation(root, info, **data)` | Your logic. Must return `cls(...)`. |
| `success(message, message_code="SUCCESS", data=None)` | Successful `ResponseField`. |
| `failure(error, field=None, data=None)` | Failed `ResponseField` from a string or any object with `.message`/`.code`. |
| `add_field_error(field_name, error, index=None)` | Field-scoped failure; `index` adds "at N" for list inputs. |
| `add_response(message, message_code, status, field, data, errors)` | Low-level builder. |
| `_validate_required_fields(data, field_map, errors, prefix="")` | Append an error for each empty field (`None`, blank, empty list/dict). |
| `_validate_file(file, field_name, errors, idx=None, allowed_types=None, max_file_size=None, ...)` | `validate_file` that appends a field error. |
| `handle_exception(exc)` | Override to change how unexpected exceptions become responses. |

### Lists: `graphene_shield.queries`, `.connections`, `.filters`, `.sorting`

| Name | Description |
|---|---|
| `ModelListField(node_type, filter_input=None, search_fields=(), sort_input=SortingInput, distinct=False, pagination=None, max_limit=..., on=None, enforce_first_or_last=False)` | List field that applies filters, search, sort and pagination. |
| `FilterConnectionField(node_type, pagination=None, max_limit=..., on=None, enforce_first_or_last=False)` | The same, without filters, search and sort. |
| `CountableDjangoObjectType` | `DjangoObjectType` with a `totalCount` connection and a `number` (primary key) field. |
| `IdFilterInput`, `BaseFilterInput` | Filter inputs. Subclass them and override `filter_queryset(queryset, filters)`; `common_field_map` renames columns. |
| `make_sort_field_enum(name, {"KEY": ["field", "pk"]})` | Sort-field enum; every listed field is used in order. |
| `make_sorting_input(name, enum)`, `SortingInput`, `OrderDirection` | Sort input types. `SortingInput` offers `ID`, `CREATED_AT`, `UPDATED_AT`, `NAME`. |
| `resolve_model_list(queryset, *, filter_input, filters, search, search_fields, sort_by, distinct)` | Filter, search and sort a queryset yourself. |

### Files: `graphene_shield.files`

| Name | Description |
|---|---|
| `validate_file(file, allowed_types=None, max_file_size=None, check_file_size=True, validate_content=True, error_messages=None)` | Returns the failing error message, or `None` if valid. |
| `detect_file_type(file)` | `("PDF", "application/pdf")`, or `(None, mime)` when unknown or inconsistent. |
| `contains_pdf_active_content(file)` | `True` for active content, or for files that can't be inspected. |
| `DEFAULT_MAX_FILE_SIZE`, `DEFAULT_ALLOWED_TYPES` | 10 MB and `{"PDF"}`. |
| `MAX_PDF_INFLATED_BYTES`, `MAX_PDF_OBJECTS` | Safety limits for the PDF check: 64 MB inflated, 500,000 objects. |

### Authentication: `graphene_shield.authentication`, `graphene_shield.auth`

| Name | Description |
|---|---|
| `authentication.login_required` | Resolver decorator using `AUTHENTICATOR`. |
| `authentication.authenticate(request, authenticator=None)` | Authenticate, run `USER_VALIDATOR`, set `request.user`; cached per request. |
| `authentication.session_authenticate(request)` | Authenticator for Django sessions or any middleware that sets `request.user`. |
| `auth.jwt_required` | Resolver decorator that always uses JWT (`jwt` extra). |
| `auth.authenticate_request(request)` | The default JWT authenticator. |
| `auth.decode_token(token)`, `auth.get_token_from_request(request)` | Lower-level JWT helpers raising coded errors. |

### Views, responses and helpers

| Name | Description |
|---|---|
| `views.GraphQLView`, `views.FileUploadGraphQLView` | Views with the error envelope and depth limit; the second one handles multipart uploads (`upload` extra). |
| `responses.ResponseField` | `message`, `messageCode`, `status`, `field`, `data`, `errors`. |
| `responses.EnhancedGraphQLError(message, message_code=None, status=None, data=None)` | A `GraphQLError` carrying a code; `.from_message(error)` builds one from an `ErrorMessage`. |
| `messages.ErrorMessage(message, code)` | Error definition. The defaults are in `CommonErrorMessages`, `FileErrorMessages`, `AuthErrorMessages` and `JwtErrorMessages`. |
| `utils.resolve_global_id(global_id, expected_type=None)` | Decode a Relay ID, raising `ValueError` if it is invalid. |
| `utils.sanitize_input(value, *, unescape_html=False)`, `utils.is_effectively_empty(value)` | The input helpers `BaseMutation` uses. |

The package logs through the standard `logging` module under the `graphene_shield.mutations`, `graphene_shield.views`, `graphene_shield.queries` and `graphene_shield.files` loggers.

## Limitations

- **Graphene-Django only.** It isn't for Strawberry, Ariadne or REST APIs.
- **N+1 queries.** Prevention is limited to the optional `graphene-django-optimizer`. Use `select_related`/`prefetch_related` in your resolvers or `get_queryset`.
- **The error envelope** returns one error body and drops partial `data`. Use graphene-django's own view if your clients rely on GraphQL's standard `errors` array.
- **Query limits.** Only depth is limited. There are no alias, batch or query-cost limits; use rate limiting for those.
- **Keyset pagination** can't jump to a page, needs non-null sort fields and plain field-name ordering, and can't combine `first` with `last`.
- **Upload checks.** Without `pypdf`, the PDF check is a heuristic. Macros in legacy `.doc`/`.xls`/`.ppt` files and CSV formula injection are not detected.
- **Permissions.** There are no field-level permissions: scope data in list resolvers and in `get_queryset` (which `node(id:)` also uses).
- **Async and subscriptions.** Neither async resolvers nor subscriptions are supported.
- **Versions.** Python 3.10–3.13 and Django 4.2/5.2 are tested. The test suite also passes on Django 6.1, but graphene-django hasn't committed to supporting Django 6.

## License

MIT. See [LICENSE](LICENSE).
