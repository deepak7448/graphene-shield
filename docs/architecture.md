# How graphene-shield works

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

## Pagination details

- **`COUNT(*)` is lazy in every mode.** It only runs when the client selects `totalCount` or `totalPages`. Has-next is found by fetching one extra row.
- **Stable order.** An unordered queryset is ordered by primary key, so pages never overlap. Keyset always adds the primary key as a final tie-breaker.
- **Keyset rules.**
  - It follows the queryset's ordering: `sortBy`, an `order_by(...)` in your resolver, or the model's `Meta.ordering`. That ordering must use plain field names.
  - A sort field that is empty (`NULL`) can't be paged past, so sort on non-null fields.
  - Each cursor records the sort order it was made for. A cursor reused with a different `sortBy` gets a `BAD_REQUEST`, and so do tampered cursors.
  - `first` and `last` can't be combined.
- **Page mode.** `pageSize` defaults to `DEFAULT_PAGE_SIZE` (20) and is capped by `max_limit`. The type is named `<Node>Page`, for example `AuditTypePage`.

## File validation details

Recognised types: PDF, PNG, JPEG, GIF, ICO, TXT/CSV, DOCX, XLSX, PPTX, DOC, XLS and PPT. DOC, XLS and PPT need the `files` extra. A file whose header claims one format but whose structure does not match is rejected. These are always rejected:

- macro-enabled Office files (`.docm`, `.xlsm`, `.pptm`);
- text files that contain HTML, SVG or XML markup, since a browser could run them;
- PDFs containing `/JavaScript`, `/JS`, `/OpenAction`, `/AA`, `/Launch`, `/SubmitForm`, `/ImportData`, `/GoToR`, `/GoToE`, `/RichMedia`, `/XFA` or embedded files. Digital signatures are not checked, so a signed PDF with active content is rejected too.

With `pypdf` installed (the `files` extra), the PDF check walks every object a PDF reader would use, including compressed object streams and `#`-escaped names. Without it, a byte-level scan of the file's dictionaries runs instead, which is a heuristic. Either way, files that can't be inspected (unparseable, password-protected, unreadable object streams, compression bombs) are rejected.

Not covered: macros in legacy `.doc`/`.xls`/`.ppt` files, and CSV formula injection (`=HYPERLINK(...)` cells). Handle those where the files are opened or exported.

## JWT checks

The JWT authenticator verifies the token's signature, `exp`, algorithm and (optionally) audience and issuer. It rejects tokens whose `jti` is in the cache under `JWT_BLACKLIST_CACHE_PREFIX`, and tokens whose `token_type` claim isn't `access` (so a refresh token can't be used as an access token). Then it loads the user, rejects inactive accounts, and sets `request.user` and `request.jwt_payload`.

Error codes: `PERMISSION_DENIED`, `INVALID_FORMAT`, `TOKEN_EXPIRED`, `TOKEN_REVOKED`, `WRONG_TOKEN_TYPE`, `INVALID_SIGNATURE`, `INVALID_ALGORITHM`, `INVALID_AUDIENCE`, `INVALID_ISSUER`, `TOKEN_INVALID`, `PAYLOAD_INVALID`, `USER_NOT_FOUND` and `USER_INACTIVE`.

## Projects without authentication

Authentication is opt-in. Without `login_required`, `@login_required` or `@jwt_required`, nothing is checked and PyJWT isn't needed. That also means **everything is public**:

- `ModelListField` without a resolver returns every row of the model's default manager, including inactive or soft-deleted rows.
- `node(id: ...)` returns any object by ID.
- Every mutation can be called by anyone.

If some data is private, scope it explicitly: return a filtered queryset from the list resolver, and override `get_queryset` on the type (it is also used by `node`). If authentication is misconfigured (e.g. `login_required = True` but PyJWT missing), requests are rejected and the cause is logged; nothing is let through.
