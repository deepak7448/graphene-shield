"""JWT guards for resolvers and mutations (requires ``graphene-shield[jwt]``).

``@jwt_required`` works on any resolver signature that receives a
``GraphQLResolveInfo`` — query resolvers ``(root, info, ...)`` and mutation
classmethods ``(cls, root, info, ...)`` alike::

    class Query(graphene.ObjectType):
        me = graphene.Field(UserType)

        @jwt_required
        def resolve_me(root, info):
            return info.context.user

    class UpdateProfile(BaseMutation):
        @classmethod
        @jwt_required
        def perform_mutation(cls, root, info, **data): ...

On success ``request.user`` is the authenticated user and ``request.jwt_payload``
the decoded claims. Failures raise ``EnhancedGraphQLError`` with a
``JwtErrorMessages`` code. ``@jwt_required`` also runs the ``USER_VALIDATOR``
setting; calling ``authenticate_request`` directly only checks the token.
"""

from __future__ import annotations

from functools import wraps
from typing import Any

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError

from graphene_shield.authentication import authenticate, find_info
from graphene_shield.conf import shield_settings
from graphene_shield.messages import JwtErrorMessages
from graphene_shield.responses import EnhancedGraphQLError


def _fail(error) -> EnhancedGraphQLError:
    return EnhancedGraphQLError.from_message(error)


def _accepted_prefixes() -> set[str]:
    prefixes = shield_settings.JWT_AUTH_HEADER_PREFIX
    if isinstance(prefixes, str):
        prefixes = [prefixes]
    # The auth scheme is case-insensitive (RFC 7235 §2.1).
    return {p.lower() for p in prefixes}


def get_token_from_request(request) -> str:
    """Read the token from ``JWT_AUTH_HEADER_NAME``.

    Accepts ``<prefix> <token>`` for any prefix in ``JWT_AUTH_HEADER_PREFIX``
    (default ``Bearer`` and ``JWT``, any case), and a bare ``<token>`` when
    that setting includes ``""``.
    """
    header = request.META.get(shield_settings.JWT_AUTH_HEADER_NAME, "")
    if not header:
        raise _fail(JwtErrorMessages.PERMISSION_DENIED)
    parts = header.split()
    prefixes = _accepted_prefixes()
    if len(parts) == 2 and parts[0].lower() in prefixes:
        return parts[1]
    if len(parts) == 1 and "" in prefixes:
        return parts[0]
    raise _fail(JwtErrorMessages.INVALID_FORMAT)


def decode_token(token: str) -> dict[str, Any]:
    """Verify and decode ``token``, mapping each PyJWT failure to its own error code."""
    options = {"require": ["exp"]}
    try:
        return jwt.decode(
            token,
            shield_settings.JWT_SIGNING_KEY or settings.SECRET_KEY,
            algorithms=shield_settings.JWT_ALGORITHMS,
            audience=shield_settings.JWT_AUDIENCE,
            issuer=shield_settings.JWT_ISSUER,
            leeway=shield_settings.JWT_LEEWAY,
            options=options,
        )
    # Every specific error below subclasses InvalidTokenError, so it must come last.
    except jwt.ExpiredSignatureError:
        raise _fail(JwtErrorMessages.TOKEN_EXPIRED) from None
    except jwt.InvalidSignatureError:
        raise _fail(JwtErrorMessages.INVALID_SIGNATURE) from None
    except jwt.InvalidAlgorithmError:
        raise _fail(JwtErrorMessages.INVALID_ALGORITHM) from None
    except jwt.InvalidAudienceError:
        raise _fail(JwtErrorMessages.INVALID_AUDIENCE) from None
    except jwt.InvalidIssuerError:
        raise _fail(JwtErrorMessages.INVALID_ISSUER) from None
    except jwt.InvalidTokenError:
        raise _fail(JwtErrorMessages.TOKEN_INVALID) from None


def is_token_revoked(payload: dict[str, Any]) -> bool:
    prefix = shield_settings.JWT_BLACKLIST_CACHE_PREFIX
    jti = payload.get("jti")
    return bool(prefix is not None and jti and cache.get(f"{prefix}{jti}"))


def authenticate_request(request):
    """Authenticate ``request`` from its JWT and return the user.

    This is the default ``AUTHENTICATOR``. It checks the token and the user's
    ``is_active`` flag only; ``USER_VALIDATOR`` is run by
    ``authentication.authenticate`` (used by ``login_required`` and ``@jwt_required``).
    """
    payload = decode_token(get_token_from_request(request))
    if is_token_revoked(payload):
        raise _fail(JwtErrorMessages.TOKEN_REVOKED)

    type_claim = shield_settings.JWT_TOKEN_TYPE_CLAIM
    if type_claim and type_claim in payload:
        if payload[type_claim] not in shield_settings.JWT_ALLOWED_TOKEN_TYPES:
            raise _fail(JwtErrorMessages.WRONG_TOKEN_TYPE)

    user_id = payload.get(shield_settings.JWT_USER_ID_CLAIM)
    if user_id is None:
        raise _fail(JwtErrorMessages.PAYLOAD_INVALID)

    User = get_user_model()
    try:
        user = User._default_manager.get(**{shield_settings.JWT_USER_ID_FIELD: user_id})
    except (User.DoesNotExist, ValueError, TypeError, ValidationError):
        raise _fail(JwtErrorMessages.USER_NOT_FOUND) from None
    if not getattr(user, "is_active", True):
        raise _fail(JwtErrorMessages.USER_INACTIVE)

    request.user = user
    request.jwt_payload = payload
    return user


def jwt_required(func):
    """Guard a resolver with JWT, whatever ``AUTHENTICATOR`` is set to."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        authenticate(find_info(args, kwargs).context, authenticator=authenticate_request)
        return func(*args, **kwargs)

    return wrapper


# Names used by projects migrating from in-house decorators.
jwt_login_required_query = jwt_required
jwt_login_required_mutation = jwt_required
