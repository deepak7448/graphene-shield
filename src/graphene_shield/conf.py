"""Package settings, read from ``settings.GRAPHENE_SHIELD``.

Example::

    GRAPHENE_SHIELD = {
        "CONNECTION_MAX_LIMIT": 100,
        "EXPOSE_EXCEPTION_MESSAGES": DEBUG,
        "JWT_ALGORITHMS": ["HS256"],
    }
"""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.utils.module_loading import import_string

DEFAULTS: dict[str, Any] = {
    # ── Mutations ──
    # When False, unexpected exceptions raised inside ``perform_mutation`` are
    # logged and the client receives ``UNEXPECTED_ERROR_MESSAGE`` instead of
    # ``str(exc)`` (which can leak SQL, paths, or internal state).
    "EXPOSE_EXCEPTION_MESSAGES": False,
    "UNEXPECTED_ERROR_MESSAGE": "Something went wrong. Please try again later.",
    "UNEXPECTED_ERROR_CODE": "UNEXPECTED_ERROR",
    # Strip surrounding whitespace from string arguments before validation
    # (values under password/secret/token keys are left untouched).
    "SANITIZE_INPUT": True,
    # Also HTML-unescape them ("&lt;b&gt;" -> "<b>"). Off: it defeats upstream
    # escaping and alters data that really contains entities.
    "UNESCAPE_HTML_INPUT": False,
    # ── Connections ──
    "CONNECTION_MAX_LIMIT": 1000,
    # Default pagination for FilterConnectionField / ModelListField:
    # "offset" (Relay cursors over LIMIT/OFFSET, plus an `offset` argument),
    # "keyset" (Relay cursors holding sort values; fast on deep pages), or
    # "page" (`page`/`pageSize` -> items + pageInfo). Override per field with
    # pagination="...".
    "PAGINATION": "offset",
    # Page size for "page" pagination when the client sends no pageSize.
    "DEFAULT_PAGE_SIZE": 20,
    # ── Views ──
    # Reject operations nested deeper than this (introspection is exempt).
    # None disables the limit.
    "MAX_QUERY_DEPTH": 15,
    # ── Authentication ──
    # ``fn(request) -> user`` used by ``BaseMutation.login_required`` and
    # ``@login_required``; raise ``EnhancedGraphQLError`` to reject. Use
    # "graphene_shield.authentication.session_authenticate" for Django
    # sessions (no JWT), or your own function for anything else.
    "AUTHENTICATOR": "graphene_shield.auth.authenticate_request",
    # Dotted path (or callable) ``fn(user, payload, request)`` run after any
    # authenticator succeeds; ``payload`` is the JWT claims or None. Raise
    # ``EnhancedGraphQLError`` to reject the request.
    "USER_VALIDATOR": None,
    # ── JWT ──
    "JWT_SIGNING_KEY": None,  # falls back to settings.SECRET_KEY
    "JWT_ALGORITHMS": ["HS256"],
    "JWT_AUDIENCE": None,
    "JWT_ISSUER": None,
    "JWT_LEEWAY": 0,
    # request.META key holding the token ("HTTP_X_AUTH_TOKEN" for X-Auth-Token).
    "JWT_AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
    # Accepted prefixes (case-insensitive); a string or a list. Add "" to also
    # accept a bare token with no prefix.
    "JWT_AUTH_HEADER_PREFIX": ["Bearer", "JWT"],
    "JWT_USER_ID_CLAIM": "user_id",
    "JWT_USER_ID_FIELD": "pk",
    # Tokens carrying this claim must have one of the allowed values, so e.g. a
    # long-lived refresh token can't be used as an access token. Tokens without
    # the claim are accepted. None disables the check.
    "JWT_TOKEN_TYPE_CLAIM": "token_type",
    "JWT_ALLOWED_TOKEN_TYPES": ["access"],
    # Cache key prefix for revoked token ids (``jti``). None disables the check.
    "JWT_BLACKLIST_CACHE_PREFIX": "blacklist:",
}

IMPORT_STRINGS = {"AUTHENTICATOR", "USER_VALIDATOR"}
# Old name -> new name, for a helpful error.
RENAMED = {"JWT_USER_VALIDATOR": "USER_VALIDATOR"}


class ShieldSettings:
    def __init__(self, defaults: dict[str, Any] | None = None):
        self.defaults = defaults or DEFAULTS
        self._cached: set[str] = set()

    @property
    def user_settings(self) -> dict[str, Any]:
        if not hasattr(self, "_user_settings"):
            user_settings = getattr(django_settings, "GRAPHENE_SHIELD", {}) or {}
            unknown = sorted(set(user_settings) - set(self.defaults))
            if unknown:
                hints = [
                    f"{key} (renamed to {RENAMED[key]})" if key in RENAMED else key
                    for key in unknown
                ]
                raise ImproperlyConfigured(
                    f"Unknown GRAPHENE_SHIELD setting(s): {', '.join(hints)}"
                )
            self._user_settings = user_settings
        return self._user_settings

    def __getattr__(self, name: str) -> Any:
        if name not in self.defaults:
            raise AttributeError(f"Invalid graphene-shield setting: {name!r}")
        value = self.user_settings.get(name, self.defaults[name])
        if name in IMPORT_STRINGS and isinstance(value, str):
            value = import_string(value)
        self._cached.add(name)
        setattr(self, name, value)
        return value

    def reload(self) -> None:
        for name in self._cached:
            delattr(self, name)
        self._cached.clear()
        if hasattr(self, "_user_settings"):
            delattr(self, "_user_settings")


shield_settings = ShieldSettings()


def _reload(*, setting, **kwargs):
    if setting == "GRAPHENE_SHIELD":
        shield_settings.reload()


setting_changed.connect(_reload)
