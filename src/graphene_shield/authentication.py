"""Authentication that works with any login method (no PyJWT needed).

``BaseMutation.login_required`` and the ``@login_required`` decorator call the
function named by the ``AUTHENTICATOR`` setting with the request. It must
return the user, or raise ``EnhancedGraphQLError`` to reject the request.

Built in:

- ``graphene_shield.auth.authenticate_request`` (default): JWT from the
  ``Authorization`` header (needs ``graphene-shield[jwt]``).
- ``graphene_shield.authentication.session_authenticate``: trusts
  ``request.user`` as set by Django's session middleware, or by any other
  middleware that authenticates the request.

For anything else (API keys, cookies, an external identity provider) point
``AUTHENTICATOR`` at your own ``fn(request) -> user``.

Whichever authenticator runs, the ``USER_VALIDATOR`` setting is then called as
``fn(user, payload, request)`` for app-specific rules (email verified, account
suspended...). ``payload`` is the decoded JWT for the JWT authenticator and
``None`` otherwise. Raise ``EnhancedGraphQLError`` to reject the request.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from graphql import GraphQLError, GraphQLResolveInfo

from graphene_shield.conf import shield_settings
from graphene_shield.messages import AuthErrorMessages
from graphene_shield.responses import EnhancedGraphQLError


def session_authenticate(request):
    """Accept the user Django's middleware already put on the request."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise EnhancedGraphQLError.from_message(AuthErrorMessages.PERMISSION_DENIED)
    if not getattr(user, "is_active", True):
        raise EnhancedGraphQLError.from_message(AuthErrorMessages.USER_INACTIVE)
    return user


# Per-request memo of results, keyed by authenticator: a query with several
# guarded fields authenticates (token decode, user lookup, validator) only once.
_RESULTS_ATTR = "_graphene_shield_auth"


def authenticate(request, authenticator=None):
    """Authenticate ``request``, run ``USER_VALIDATOR``, and set ``request.user``.

    ``authenticator`` defaults to the ``AUTHENTICATOR`` setting. The outcome is
    remembered for the rest of the request, so every guarded field after the
    first is free. If the validator rejects the user, ``request.user`` is put
    back to what it was, so other fields in the same query don't see the
    rejected user as logged in.
    """
    if authenticator is None:
        authenticator = _configured_authenticator()

    results = getattr(request, _RESULTS_ATTR, None)
    if results is None:
        results = {}
        setattr(request, _RESULTS_ATTR, results)
    if authenticator in results:
        outcome = results[authenticator]
        if isinstance(outcome, GraphQLError):
            raise outcome
        user, payload = outcome
        request.user, request.jwt_payload = user, payload
        return user

    previous_user = getattr(request, "user", None)
    previous_payload = getattr(request, "jwt_payload", None)
    request.jwt_payload = None  # set by the JWT authenticator; None for others
    try:
        user = authenticator(request)
        validator = shield_settings.USER_VALIDATOR
        if validator is not None:
            validator(user, request.jwt_payload, request)
    except BaseException as exc:
        request.user = previous_user if previous_user is not None else AnonymousUser()
        request.jwt_payload = previous_payload
        if isinstance(exc, GraphQLError):  # a definite "no"; anything else may be transient
            results[authenticator] = exc
        raise
    request.user = user
    results[authenticator] = (user, request.jwt_payload)
    return user


def _configured_authenticator():
    try:
        return shield_settings.AUTHENTICATOR
    except ImportError as exc:
        raise ImproperlyConfigured(
            "The AUTHENTICATOR setting could not be imported. The default JWT "
            "authenticator needs `pip install graphene-shield[jwt]`; projects "
            "without JWT can set AUTHENTICATOR to "
            "'graphene_shield.authentication.session_authenticate'."
        ) from exc


def find_info(args, kwargs) -> GraphQLResolveInfo:
    info = kwargs.get("info")
    if isinstance(info, GraphQLResolveInfo):
        return info
    for arg in args:
        if isinstance(arg, GraphQLResolveInfo):
            return arg
    raise TypeError("Could not find the GraphQLResolveInfo argument of the decorated resolver.")


def login_required(func):
    """Guard a resolver with the configured ``AUTHENTICATOR``."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        authenticate(find_info(args, kwargs).context)
        return func(*args, **kwargs)

    return wrapper
