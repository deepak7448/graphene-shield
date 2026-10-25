# Changelog

## 0.1.0 — 2026-10-24

First release, extracted from the GCC backend's `core/base_configure` layer.

Fixes compared with the in-house code:

- `perform_mutation` exceptions no longer send `str(exc)` to clients by default (`EXPOSE_EXCEPTION_MESSAGES`).
- `perform_mutation` returning `None`, or not being implemented, now raises an error instead of returning `null`.
- `GraphQLError`s raised inside mutations (e.g. auth failures) are no longer swallowed into `response`.
- JWT tokens are decoded once. Expired, bad-signature, wrong-algorithm, wrong-audience and wrong-issuer tokens each get their own code, and an expired token no longer escapes as a raw `ExpiredSignatureError`. The signing key, algorithms, claim names and revocation prefix are configurable. Inactive users are rejected.
- Pagination limits are enforced with coded GraphQL errors instead of `assert` (which `python -O` removes). Negative `first`/`last`/`offset` values are rejected.
- Multi-field sort enums (`["created_at", "pk"]`) now order by every field, not only the first.
- `totalCount` reuses the count already computed for pagination instead of re-evaluating the queryset.
- `ResponseField.to_dict()` now includes `field` and `errors`. The error view lists every error message, not only the first.
- Removed the per-node `version` field (it started two `git` subprocesses for every row) and the domain-specific `requestsCount` field (it referenced an undefined `ApprovalStatus`).
- Filtering on `isActive`/`createdAt`/`updatedAt` for a model without that column now returns `BAD_REQUEST`. The old code silently ignored the filter and returned unfiltered rows. Added `IdFilterInput` for such models, and `common_field_map` to remap column names.
- Sorting on a field the model lacks now returns `INVALID_SORT_FIELD`. Before, it surfaced as an uncoded, masked error during pagination.
- File limits are function arguments, not settings: `validate_file(max_file_size=..., allowed_types=...)` and `contains_pdf_active_content(max_scan_bytes=...)`. The defaults (10 MB, PDF only, 1 MB scan) are `files.DEFAULT_MAX_FILE_SIZE`, `DEFAULT_ALLOWED_TYPES` and `DEFAULT_PDF_SCAN_BYTES`.
- Removed the digital-signature exemption from the PDF active-content check. The old code accepted any PDF containing signature marker text, which anyone could fake by appending `/ByteRange`. Only active content is checked now, so a signed PDF with JavaScript is rejected like any other.

Security review fixes:

- **PDF active-content check rebuilt.** The old check could be bypassed by writing actions without spaces (`/A<</S/JavaScript/JS(...)>>`), by `#`-escaped names (`/J#61vaScript`), by hiding dictionaries in compressed object streams, or by placing JavaScript in the middle of a file larger than the old 1 MB head + 200 KB tail window. It now parses the PDF with `pypdf` (in the `files` extra), plus a byte scan of the file's dictionaries. Stream bodies are skipped, because random image bytes often look like `/JS `. Files that can't be inspected are rejected. `/XFA`, `/SubmitForm`, `/ImportData`, `/GoToR` and `/GoToE` are now detected too.
- Macro-enabled Office files (`vbaProject.bin` inside the zip) are rejected instead of passing as DOCX/XLSX/PPTX.
- HTML/SVG/XML content is no longer accepted as TXT, and libmagic's `text/html` is no longer mapped to TXT.
- All JPEG variants are recognised (any `FF D8 FF` header). Valid photos were being rejected before.
- `allowed_types=[]` now allows nothing. Before, it silently fell back to PDF.
- `perform_mutation` runs in `transaction.atomic()` by default. Before, an exception after a partial write left that data committed.
- Exceptions raised in `validate_input` are handled like those in `perform_mutation`.
- New `login_required` mutation attribute authenticates before validation, so anonymous callers can't probe validation results (e.g. "email already registered").
- Input sanitising keeps `InputObjectType` values intact. Before, they became plain dicts and `data.input.name` broke. It no longer touches values under `password`/`secret`/`token` keys, and no longer HTML-unescapes by default (new `UNESCAPE_HTML_INPUT` setting). Unescaping turned `&lt;script&gt;` into `<script>`, which defeats upstream escaping.
- JWT: tokens whose `token_type` claim isn't `access` are rejected (`WRONG_TOKEN_TYPE`), so refresh tokens can't be used as access tokens. The `Bearer` scheme is matched case-insensitively. A malformed user id in a token gives `USER_NOT_FOUND` instead of an unhandled `ValidationError`.
- Pagination: an invalid `after`/`before` cursor is a `BAD_REQUEST`. Before, combining it with `offset` crashed with a `TypeError`. `first: 0` now counts as given for `enforce_first_or_last`.
- Views: operations deeper than `MAX_QUERY_DEPTH` (default 15) are rejected.
- An unknown key in `GRAPHENE_SHIELD` raises `ImproperlyConfigured`. Before, a typo such as `EXPOSE_EXCEPTION_MESSAGE` was silently ignored.
- Requires `hatchling>=1.27` to build (needed for the PEP 639 `license` field).

Authentication:

- `JWT_AUTH_HEADER_PREFIX` accepts a list, and defaults to `["Bearer", "JWT"]` (any case). `JWT <token>`, the django-graphql-jwt default, used to be rejected. Add `""` to accept a bare token with no prefix.
- New `JWT_AUTH_HEADER_NAME` setting for tokens sent in another header (e.g. `X-Auth-Token`).
- New `AUTHENTICATOR` setting and `graphene_shield.authentication` module (no PyJWT needed). `login_required` and the new `@login_required` decorator work with JWT (default), Django sessions (`session_authenticate`), or your own function. Before, `login_required` only understood JWT and rejected users logged in through a session.
- Using the default JWT authenticator without PyJWT installed raises a clear `ImproperlyConfigured` that names the fix.- `JWT_USER_VALIDATOR` is renamed to `USER_VALIDATOR` and runs for every authenticator (JWT, sessions, custom), once per check. `payload` is `None` for non-JWT authenticators. The old name raises an error that names the new one. If the validator rejects a user, `request.user` is restored, so other fields in the same query don't see that user as logged in.

Pagination:

- New `PAGINATION` setting and per-field `pagination=` argument with three modes:
  - `"offset"`: the existing Relay connection with `offset`.
  - `"keyset"`: Relay cursors that hold the last row's sort values. It stays fast on deep pages and never skips or repeats rows when data changes.
  - `"page"`: `page`/`pageSize` returning `items` and `pageInfo { page pageSize totalCount totalPages hasNextPage hasPreviousPage }`, plus a `DEFAULT_PAGE_SIZE` setting.
- `COUNT(*)` only runs when `totalCount`/`totalPages` is selected. Before, forward pages always ran it. With `first`/`after`, `hasPreviousPage` is now `true` after the first page (the Relay spec allows this).
- Unordered querysets are ordered by primary key before paginating, so pages can't overlap.

Performance:

- PDF validation is about 17× faster on an 8 MB file (124 ms → 7 ms). Upload content is read once instead of twice. The byte scan locates streams in one linear pass instead of two regex passes, and checks every marker in one search. libmagic only runs when the file signature can't decide the type.
- Authentication is cached per request. A query with several guarded fields decodes the token, loads the user and runs `USER_VALIDATOR` once instead of once per field (5 fields: 5 user queries → 1). Rejections are cached too; errors that might be temporary are not.
- The error envelope no longer re-parses successful responses, which are most responses.
- Hardening: the PDF byte scan only treats `stream` as a stream when it follows a dictionary's `>>`. A name such as `/upstream` can no longer hide the dictionary after it.
