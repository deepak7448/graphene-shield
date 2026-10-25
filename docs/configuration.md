# Configuration

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

## Recipes

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

## Reference

### Errors and mutations

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `EXPOSE_EXCEPTION_MESSAGES` | `False` | When an unexpected exception happens in a mutation or resolver, clients see `UNEXPECTED_ERROR_MESSAGE`; the real error is logged. `True` sends `str(exc)` instead. | Development only, e.g. `DEBUG` |
| `UNEXPECTED_ERROR_MESSAGE` | `"Something went wrong. Please try again later."` | The message used instead of a hidden exception. | You want different wording or a translation key |
| `UNEXPECTED_ERROR_CODE` | `"UNEXPECTED_ERROR"` | `messageCode` of that failed mutation response. | Your clients expect another code |
| `SANITIZE_INPUT` | `True` | Strips whitespace around every string argument before `validate_input`. Values under keys containing `password`, `secret` or `token` are never touched. | You need leading/trailing spaces kept everywhere |
| `UNESCAPE_HTML_INPUT` | `False` | Also turns `&amp;` into `&` (repeatedly) in string arguments. Off because it undoes escaping a client or proxy did on purpose. | A legacy client double-escapes text |

### Lists and pagination

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `PAGINATION` | `"offset"` | Default style for `ModelListField`/`FilterConnectionField`: `"offset"`, `"keyset"` or `"page"` (see [Pagination](https://github.com/deepak7448/graphene-shield/blob/master/README.md#pagination)). A field's `pagination=` argument overrides it. | You want keyset or page numbers everywhere |
| `CONNECTION_MAX_LIMIT` | `1000` | Largest `first`/`last`/`pageSize` a client may request, and the page size of a connection when the client gives none. A field's `max_limit=` overrides it. `None` removes the cap; don't do that on a public API. | Lower it for mobile/public APIs |
| `DEFAULT_PAGE_SIZE` | `20` | `pageSize` when a `"page"` field is called without one (never above `max_limit`). | Your UI shows a different number of rows |

### Query limits

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `MAX_QUERY_DEPTH` | `15` | Shield views reject operations nested deeper than this before running them. Introspection is exempt. `None` disables it. | Your schema legitimately nests deeper, or you want it stricter |

### Authentication

| Setting | Default | What it does | Change it when |
|---|---|---|---|
| `AUTHENTICATOR` | `"graphene_shield.auth.authenticate_request"` (JWT) | `fn(request) -> user` used by `login_required = True` and `@login_required`. Raise `EnhancedGraphQLError` to reject. The result is cached for the rest of the request. | Sessions (`...authentication.session_authenticate`), API keys, cookies, SSO |
| `USER_VALIDATOR` | `None` | `fn(user, payload, request)` run once per request after any authenticator succeeds. `payload` is the JWT claims, or `None`. Raise to reject. | App rules: email verified, account suspended, password changed |

### JWT (only used by the JWT authenticator and `@jwt_required`)

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

### Not settings: per-call arguments

File validation limits are passed per upload, because different uploads need different rules:

```python
validate_file(upload, allowed_types={"PDF", "PNG"}, max_file_size=5 * 1024 * 1024)
# defaults: files.DEFAULT_ALLOWED_TYPES ({"PDF"}) and files.DEFAULT_MAX_FILE_SIZE (10 MB)
```
