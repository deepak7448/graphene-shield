import datetime as dt

import jwt
import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from graphene_shield.responses import EnhancedGraphQLError
from tests.conftest import FakeRequest

SECRET = "test-secret-key-that-is-long-enough-for-hs256"


def make_token(user_id=1, key=SECRET, algorithm="HS256", minutes=5, **claims):
    payload = {
        "user_id": user_id,
        "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes),
        **claims,
    }
    return jwt.encode(payload, key, algorithm=algorithm)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="ada", password="x")


def code(result):
    return result.errors[0].extensions["message_code"]


def test_valid_token(execute, user):
    result = execute("{ whoami }", request=FakeRequest(make_token(user.pk)))
    assert result.errors is None
    assert result.data["whoami"] == "ada"


@pytest.mark.parametrize(
    "request_kwargs, expected",
    [
        ({}, "PERMISSION_DENIED"),
        ({"header": "Token abc"}, "INVALID_FORMAT"),
        ({"header": "Bearer"}, "INVALID_FORMAT"),
        ({"header": "abc"}, "INVALID_FORMAT"),  # bare tokens are opt-in
        ({"header": "Bearer a b"}, "INVALID_FORMAT"),
        ({"token": "garbage"}, "TOKEN_INVALID"),
    ],
)
def test_header_failures(execute, db, request_kwargs, expected):
    assert code(execute("{ whoami }", request=FakeRequest(**request_kwargs))) == expected


def test_expired_token_gets_expired_code(execute, user):
    token = make_token(user.pk, minutes=-5)
    assert code(execute("{ whoami }", request=FakeRequest(token))) == "TOKEN_EXPIRED"


def test_bad_signature_gets_specific_code(execute, user):
    token = make_token(user.pk, key="another-secret-key-that-is-long-enough")
    assert code(execute("{ whoami }", request=FakeRequest(token))) == "INVALID_SIGNATURE"


def test_disallowed_algorithm(execute, user):
    token = make_token(user.pk, algorithm="HS512")
    assert code(execute("{ whoami }", request=FakeRequest(token))) == "INVALID_ALGORITHM"


def test_revoked_token(execute, user):
    token = make_token(user.pk, jti="abc123")
    cache.set("blacklist:abc123", True)
    try:
        assert code(execute("{ whoami }", request=FakeRequest(token))) == "TOKEN_REVOKED"
    finally:
        cache.delete("blacklist:abc123")


def test_missing_claim_and_unknown_user(execute, user):
    assert code(execute("{ whoami }", request=FakeRequest(make_token(None)))) == "PAYLOAD_INVALID"
    assert code(execute("{ whoami }", request=FakeRequest(make_token(9999)))) == "USER_NOT_FOUND"


def test_inactive_user(execute, user):
    user.is_active = False
    user.save()
    assert code(execute("{ whoami }", request=FakeRequest(make_token(user.pk)))) == "USER_INACTIVE"


def reject_ada(user, payload, request):
    if user.username == "ada":
        raise EnhancedGraphQLError(
            "Email not verified.", message_code="EMAIL_NOT_VERIFIED", status=False
        )


def test_user_validator_hook(execute, user, settings):
    settings.GRAPHENE_SHIELD = {"USER_VALIDATOR": "tests.test_auth.reject_ada"}
    assert (
        code(execute("{ whoami }", request=FakeRequest(make_token(user.pk))))
        == "EMAIL_NOT_VERIFIED"
    )


def test_guarded_mutation(execute, user):
    query = "mutation { guarded { username response { status } } }"
    result = execute(query, request=FakeRequest(make_token(user.pk)))
    assert result.data["guarded"] == {"username": "ada", "response": [{"status": True}]}

    # Auth errors are GraphQL errors, not swallowed into `response`.
    result = execute(query, request=FakeRequest())
    assert code(result) == "PERMISSION_DENIED"


def test_refresh_token_cannot_be_used_as_access_token(execute, user):
    token = make_token(user.pk, minutes=60 * 24 * 7, token_type="refresh")
    assert code(execute("{ whoami }", request=FakeRequest(token))) == "WRONG_TOKEN_TYPE"
    token = make_token(user.pk, token_type="access")
    assert execute("{ whoami }", request=FakeRequest(token)).errors is None


def test_auth_scheme_is_case_insensitive(execute, user):
    request = FakeRequest(header=f"bearer {make_token(user.pk)}")
    assert execute("{ whoami }", request=request).errors is None


@pytest.mark.parametrize(
    "header", ["Bearer {t}", "bearer {t}", "BEARER {t}", "JWT {t}", "jwt {t}", "Bearer\t {t}"]
)
def test_default_prefixes_bearer_and_jwt_any_case(execute, user, header):
    request = FakeRequest(header=header.format(t=make_token(user.pk)))
    assert execute("{ whoami }", request=request).errors is None


def test_custom_prefixes_and_bare_token(execute, user, settings):
    settings.GRAPHENE_SHIELD = {"JWT_AUTH_HEADER_PREFIX": ["Token", ""]}
    token = make_token(user.pk)
    assert execute("{ whoami }", request=FakeRequest(header=f"Token {token}")).errors is None
    assert execute("{ whoami }", request=FakeRequest(header=token)).errors is None
    # Bearer is no longer accepted once the list is replaced.
    assert code(execute("{ whoami }", request=FakeRequest(header=f"Bearer {token}"))) == (
        "INVALID_FORMAT"
    )


def test_single_string_prefix_still_works(execute, user, settings):
    settings.GRAPHENE_SHIELD = {"JWT_AUTH_HEADER_PREFIX": "Bearer"}
    token = make_token(user.pk)
    assert execute("{ whoami }", request=FakeRequest(header=f"Bearer {token}")).errors is None
    assert code(execute("{ whoami }", request=FakeRequest(header=f"JWT {token}"))) == (
        "INVALID_FORMAT"
    )


def test_custom_header_name(execute, user, settings):
    settings.GRAPHENE_SHIELD = {"JWT_AUTH_HEADER_NAME": "HTTP_X_AUTH_TOKEN"}
    request = FakeRequest()
    request.META["HTTP_X_AUTH_TOKEN"] = f"Bearer {make_token(user.pk)}"
    assert execute("{ whoami }", request=request).errors is None
