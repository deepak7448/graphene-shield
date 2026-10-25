"""Login checks for projects that don't use JWT, or use their own scheme."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured

from graphene_shield import authentication
from graphene_shield.responses import EnhancedGraphQLError
from tests.conftest import FakeRequest
from tests.test_auth import make_token

SESSION = {"AUTHENTICATOR": "graphene_shield.authentication.session_authenticate"}
SIGNUP = 'mutation { signup(email: "a@b.c") { response { status } } }'


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="ada", password="x")


def session_request(user=None):
    request = FakeRequest()
    if user is not None:
        request.user = user
    return request


def code(result):
    return result.errors[0].extensions["message_code"]


def test_default_authenticator_is_jwt(execute, user):
    assert execute("{ me }", request=FakeRequest(make_token(user.pk))).data["me"] == "ada"
    assert code(execute("{ me }", request=session_request(user))) == "PERMISSION_DENIED"


def test_session_authenticator(execute, user, settings):
    settings.GRAPHENE_SHIELD = SESSION
    assert execute("{ me }", request=session_request(user)).data["me"] == "ada"
    assert execute(SIGNUP, request=session_request(user)).errors is None
    assert code(execute("{ me }", request=session_request())) == "PERMISSION_DENIED"
    assert code(execute(SIGNUP, request=session_request())) == "PERMISSION_DENIED"


def test_session_authenticator_rejects_inactive_users(execute, user, settings):
    settings.GRAPHENE_SHIELD = SESSION
    user.is_active = False
    assert code(execute("{ me }", request=session_request(user))) == "USER_INACTIVE"


def api_key_authenticator(request):
    if request.META.get("HTTP_X_API_KEY") != "s3cret":
        raise EnhancedGraphQLError("Bad API key.", message_code="BAD_API_KEY", status=False)
    return get_user_model().objects.get(username="ada")


def test_custom_authenticator(execute, user, settings):
    settings.GRAPHENE_SHIELD = {"AUTHENTICATOR": "tests.test_authentication.api_key_authenticator"}
    request = FakeRequest()
    request.META["HTTP_X_API_KEY"] = "s3cret"
    assert execute(SIGNUP, request=request).errors is None
    assert code(execute(SIGNUP, request=FakeRequest())) == "BAD_API_KEY"


def test_missing_pyjwt_gives_a_clear_error(settings, monkeypatch):
    def broken_import(_path):
        raise ModuleNotFoundError("No module named 'jwt'")

    from graphene_shield.conf import shield_settings

    monkeypatch.setattr("graphene_shield.conf.import_string", broken_import)
    shield_settings.reload()  # drop any cached AUTHENTICATOR
    with pytest.raises(ImproperlyConfigured, match="session_authenticate"):
        authentication.authenticate(FakeRequest())


# ── USER_VALIDATOR runs for every authenticator ──

seen = {}


def record_and_reject_ada(user, payload, request):
    seen["payload"] = payload
    if user.username == "ada":
        raise EnhancedGraphQLError(
            "Email not verified.", message_code="EMAIL_NOT_VERIFIED", status=False
        )


VALIDATOR = "tests.test_authentication.record_and_reject_ada"


def test_validator_runs_with_jwt_and_gets_the_claims(execute, user, settings):
    settings.GRAPHENE_SHIELD = {"USER_VALIDATOR": VALIDATOR}
    assert code(execute("{ me }", request=FakeRequest(make_token(user.pk)))) == "EMAIL_NOT_VERIFIED"
    assert seen["payload"]["user_id"] == user.pk
    # @jwt_required runs it too
    assert code(execute("{ whoami }", request=FakeRequest(make_token(user.pk)))) == (
        "EMAIL_NOT_VERIFIED"
    )


def test_validator_runs_with_sessions_with_no_payload(execute, user, settings):
    settings.GRAPHENE_SHIELD = {**SESSION, "USER_VALIDATOR": VALIDATOR}
    assert code(execute("{ me }", request=session_request(user))) == "EMAIL_NOT_VERIFIED"
    assert code(execute(SIGNUP, request=session_request(user))) == "EMAIL_NOT_VERIFIED"
    assert seen["payload"] is None


def test_validator_runs_with_custom_authenticator(execute, user, settings):
    settings.GRAPHENE_SHIELD = {
        "AUTHENTICATOR": "tests.test_authentication.api_key_authenticator",
        "USER_VALIDATOR": VALIDATOR,
    }
    request = FakeRequest()
    request.META["HTTP_X_API_KEY"] = "s3cret"
    assert code(execute(SIGNUP, request=request)) == "EMAIL_NOT_VERIFIED"


def test_validator_runs_once_per_check(execute, user, settings):
    calls = []
    settings.GRAPHENE_SHIELD = {"USER_VALIDATOR": lambda u, p, r: calls.append(u)}
    assert execute("{ me }", request=FakeRequest(make_token(user.pk))).errors is None
    assert len(calls) == 1


def test_rejected_user_is_not_left_on_the_request(execute, user, settings):
    settings.GRAPHENE_SHIELD = {"USER_VALIDATOR": VALIDATOR}
    request = FakeRequest(make_token(user.pk))
    execute("{ me }", request=request)
    assert not request.user.is_authenticated
    assert request.jwt_payload is None


def test_old_setting_name_explains_the_rename(settings):
    from graphene_shield.conf import shield_settings

    settings.GRAPHENE_SHIELD = {"JWT_USER_VALIDATOR": VALIDATOR}
    with pytest.raises(ImproperlyConfigured, match="renamed to USER_VALIDATOR"):
        _ = shield_settings.USER_VALIDATOR


# ── once per request ──


def test_several_guarded_fields_authenticate_once(execute, user):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    request = FakeRequest(make_token(user.pk))
    with CaptureQueriesContext(connection) as ctx:
        result = execute("{ a: me b: me c: whoami d: whoami }", request=request)
    assert result.errors is None
    # `me` (default authenticator) and `whoami` (@jwt_required) both use JWT: one lookup.
    assert len(ctx.captured_queries) == 1


def test_a_rejection_is_remembered_for_the_request(execute, user, settings):
    calls = []

    def reject(user, payload, request):
        calls.append(1)
        raise EnhancedGraphQLError("No.", message_code="NOPE", status=False)

    settings.GRAPHENE_SHIELD = {"USER_VALIDATOR": reject}
    result = execute("{ a: me b: me c: me }", request=FakeRequest(make_token(user.pk)))
    assert [e.extensions["message_code"] for e in result.errors] == ["NOPE"] * 3
    assert len(calls) == 1


def test_each_request_authenticates_afresh(execute, user):
    token = make_token(user.pk)
    assert execute("{ me }", request=FakeRequest(token)).errors is None
    user.is_active = False
    user.save()
    assert code(execute("{ me }", request=FakeRequest(token))) == "USER_INACTIVE"
