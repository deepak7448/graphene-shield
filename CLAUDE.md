# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`graphene-shield` is a standalone, PyPI-published library (MIT, `src/` layout, hatchling build) that packages reusable Graphene-Django plumbing: a uniform mutation response shape, filterable/sortable/paginated list fields, content-based file-upload validation, pluggable authentication (JWT/session/custom) and an error-masking GraphQL view. It was extracted from the GCC backend's `core/base_configure` layer (see `CHANGELOG.md` for every behavioural difference from that in-house code). It is **not** part of the GCC app itself: keep it generic, with no GCC domain concepts.

User-facing docs: `README.md`, `docs/api.md` (public API tables), `docs/configuration.md` (every setting), `docs/architecture.md` (how the internals work). Update the relevant doc and `CHANGELOG.md` when behaviour or public API changes.

## Commands

Dev dependencies are a PEP 735 group (`[dependency-groups] dev`); `uv.lock` is checked in and a `.venv/` exists.

```bash
uv sync --all-extras                # or: pip install -e ".[all]" --group dev
# python-magic (in the `files` extra) needs the system libmagic: apt install libmagic1

pytest                               # all tests (DJANGO_SETTINGS_MODULE=tests.settings via pyproject)
pytest tests/test_pagination.py      # one file
pytest tests/test_files.py::test_name   # one test
pytest -k keyset                     # by keyword

ruff check .                         # lint (E, F, I, B, UP; line length 100; py310 target)
ruff format --check .                # CI enforces formatting too; `ruff format .` to fix

python -m build && python -m twine check --strict dist/*   # build and check the package
```

CI (`.github/workflows/ci.yml`) runs ruff + pytest across Python 3.10–3.13 × Django 4.2/5.2/6.1 (Django 4.2 excludes 3.13; Django 6.1 needs 3.12+), so avoid syntax/APIs newer than Python 3.10 and Django 4.2. It also has a **core-install job** that installs with no extras and imports `authentication, connections, files, filters, mutations, pagination, queries, views` and builds a schema — see the next section.

Releasing: bump `__version__` in `src/graphene_shield/__init__.py` (the single version source), then publish a GitHub release tagged `vX.Y.Z`. `publish.yml` fails if the tag doesn't match `__version__`; pre-releases go to TestPyPI. Uses Trusted Publishing (no tokens).

## Architecture rules that span files

- **Optional extras must stay optional.** `optimizer`, `upload`, `files` (pypdf + python-magic) and `jwt` (PyJWT) are imported with `try/except ImportError` and set to `None` when missing (`connections.py`, `views.py`, `files.py`). `auth.py` is the only module that hard-requires PyJWT and is reached only through the `AUTHENTICATOR` setting / `@jwt_required`. Never add a top-level import of an extra to any other module.
- **`relations.py` needs `django.contrib.contenttypes`** (importing it loads `ContentType`), so no other module may import it; users import it from their models.
- **`__init__.py` imports nothing** besides defining `__version__`, so importing the package never touches Django settings. Users import submodules explicitly.
- **Settings** live in `conf.py`: `shield_settings` reads `settings.GRAPHENE_SHIELD` lazily, caches attributes, reloads on Django's `setting_changed` signal (so `override_settings` works in tests), and raises `ImproperlyConfigured` on unknown keys. Adding a setting means adding it to `DEFAULTS` (and to `IMPORT_STRINGS` if it's a dotted path, `RENAMED` if replacing an old name), then documenting it in `docs/configuration.md`.
- **Two auth modules:** `authentication.py` is backend-agnostic (`authenticate()`, `@login_required`, `session_authenticate`; runs `AUTHENTICATOR` then `USER_VALIDATOR`, caches the result on the request, restores `request.user` on rejection). `auth.py` is the default JWT authenticator and `@jwt_required`, built on top of it.
- **Errors are coded.** Client-facing failures are `EnhancedGraphQLError` (in `responses.py`) built from `ErrorMessage(message, code)` constants grouped in `messages.py` (`CommonErrorMessages`, `FileErrorMessages`, `AuthErrorMessages`, `JwtErrorMessages`). Add new codes there rather than inline strings. Don't use `assert` for validation (stripped under `python -O`).
- **Mutation lifecycle** (`mutations.BaseMutation.mutate`): auth if `login_required` → sanitize string inputs (skipping password/secret/token keys) → `validate_input` → `perform_mutation` inside `transaction.atomic()`. `GraphQLError` passes through to top-level `errors`; any other exception is logged, rolled back and masked as `UNEXPECTED_ERROR` unless `EXPOSE_EXCEPTION_MESSAGES`.
- **List pipeline** (`queries.ModelListField` → `connections.FilterConnectionField` → `pagination.py`): resolver queryset (or default manager) → type's `get_queryset` → optimizer → filters (`filters.py`) → search (`icontains` ORed over `search_fields`) → sort (`sorting.py`) → node type's optional `prefetch_for_selection` hook → pagination. `FilterConnectionField.paginate` reads the client's selection (`selection.py`): count-only selections fetch no rows, and row fetches go through `pagination.fetch_rows` so prefetches skip the extra `hasNextPage` row. Pagination styles `offset`/`keyset`/`page`; all fetch one extra row for `hasNextPage` and only run `COUNT(*)` when `totalCount`/`totalPages` is selected. Keyset cursors are base64 JSON tied to the sort order, always tie-break on PK, and must reject tampered/mismatched cursors with `BAD_REQUEST`.
- **Relation prefetching** (`prefetch.py`) must give the same data and errors as graphene-django's own FK / one-to-one resolvers (`converter.py`: `get_node` when the target type overrides `get_queryset`, attribute access otherwise). `tests/test_relation_prefetch.py` checks this by comparing `Plain*` types (default resolvers) with `Fast*` ones in `tests/prefetch_schema.py`; keep that comparison when changing either side. With the optimizer extra, every `CountableDjangoObjectType` counts as overriding `get_queryset`.
- **Views** (`views.py`): `GraphQLView` enforces `MAX_QUERY_DEPTH` (introspection exempt) and re-encodes any errored response into a single `ResponseField`-shaped body with masked exception messages; `FileUploadGraphQLView` only exists when the `upload` extra is installed.
- **File validation is security-sensitive** (`files.py`): types come from magic bytes/container structure, libmagic only as fallback; the PDF active-content check has a pypdf object walk plus a byte-scan fallback and must **fail closed** (anything uninspectable is rejected). See `docs/architecture.md` and the CHANGELOG's security section for bypasses that were fixed — don't regress them.

## Tests

- `tests/settings.py`: in-memory SQLite, locmem cache, `tests.testapp` models (`Book`, `Tag`, `LegacyItem`, `Review`, generic `Note`, and `Author`/`Article`/`ArticleStats`/`Comment` for relation prefetching). The test app has no migrations; pytest-django creates tables directly, so adding a test model needs no migration.
- `tests/schema.py` is a shared schema exercising the library; most tests add fields/mutations there and query through the `execute` fixture in `tests/conftest.py`, which passes a `FakeRequest(token=..., header=...)` as `context_value` so authenticators see `request.META`.
- `tests/urls.py` wires the views for HTTP-level tests (`test_views.py`).
