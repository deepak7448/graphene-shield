import pytest

from graphene_shield.mutations import BaseMutation
from tests.testapp.models import Book

CREATE = """
mutation($name: String, $author: String) {
  createBook(name: $name, author: $author) {
    book { name author }
    response { message messageCode status field }
  }
}
"""


@pytest.mark.django_db
def test_success(execute):
    result = execute(CREATE, {"name": "  Dune &amp; Co ", "author": "Herbert"})
    assert result.errors is None
    data = result.data["createBook"]
    # Inputs are stripped; HTML entities are kept as sent by default.
    assert data["book"] == {"name": "Dune &amp; Co", "author": "Herbert"}
    assert data["response"] == [
        {"message": "Book created.", "messageCode": "BOOK_CREATED", "status": True, "field": None}
    ]


@pytest.mark.django_db
def test_validation_errors_short_circuit(execute):
    result = execute(CREATE, {"name": "   "})
    assert result.errors is None
    assert result.data["createBook"]["response"] == [
        {
            "message": "Name is required.",
            "messageCode": "NAME_REQUIRED",
            "status": False,
            "field": "name",
        }
    ]
    assert not Book.objects.exists()


@pytest.mark.django_db
def test_unexpected_exception_is_hidden_by_default(execute):
    response = execute(CREATE, {"name": "explode"}).data["createBook"]["response"][0]
    assert response["status"] is False
    assert response["messageCode"] == "UNEXPECTED_ERROR"
    assert "secret" not in response["message"]


@pytest.mark.django_db
def test_unexpected_exception_exposed_when_configured(execute, settings):
    settings.GRAPHENE_SHIELD = {"EXPOSE_EXCEPTION_MESSAGES": True}
    response = execute(CREATE, {"name": "explode"}).data["createBook"]["response"][0]
    assert response["message"] == "secret internal detail"


@pytest.mark.django_db
def test_graphql_errors_propagate(execute):
    result = execute(CREATE, {"name": "forbidden"})
    assert [e.message for e in result.errors] == ["Not allowed"]


@pytest.mark.django_db
def test_returning_none_is_an_error(execute):
    result = execute(CREATE, {"name": "forget"})
    assert "returned None" in result.errors[0].message


@pytest.mark.django_db
def test_perform_mutation_must_be_implemented():
    class Incomplete(BaseMutation):
        pass

    with pytest.raises(NotImplementedError):
        Incomplete.mutate(None, None)


def test_add_field_error_with_index_and_string():
    response = BaseMutation.add_field_error("files", "Bad file", index=2)
    assert response.message == "Bad file at 2"
    assert response.field == "files"
    assert response.status is False
    assert response.message_code is None


def test_field_errors_use_field_name_as_given():
    assert BaseMutation.add_field_error("confirm_password", "Required").field == "confirm_password"


@pytest.mark.django_db
def test_html_unescape_is_opt_in(execute, settings):
    settings.GRAPHENE_SHIELD = {"UNESCAPE_HTML_INPUT": True}
    result = execute(CREATE, {"name": "Dune &amp;amp; Co"})
    assert result.data["createBook"]["book"]["name"] == "Dune & Co"


def test_sanitize_keeps_input_object_attribute_access_and_passwords():
    import graphene

    from graphene_shield.utils import sanitize_input

    class SignupInput(graphene.InputObjectType):
        email = graphene.String()
        password = graphene.String()

    data = sanitize_input(
        {"input": SignupInput._meta.container({"email": " a@b.c ", "password": " p w "})}
    )
    assert data["input"].email == "a@b.c"
    assert data["input"].password == " p w "  # never altered


@pytest.mark.django_db(transaction=True)
def test_exception_rolls_back_partial_writes(execute):
    result = execute(CREATE, {"name": "write-then-explode"})
    assert result.data["createBook"]["response"][0]["messageCode"] == "UNEXPECTED_ERROR"
    assert not Book.objects.exists()


@pytest.mark.django_db
def test_validate_input_exceptions_are_handled(execute):
    result = execute(CREATE, {"name": "bad-validation"})
    assert result.errors is None
    assert result.data["createBook"]["response"][0]["messageCode"] == "UNEXPECTED_ERROR"


@pytest.mark.django_db
def test_login_required_runs_before_validation(execute):
    # validate_input would reveal "Email already registered" to anyone.
    result = execute('mutation { signup(email: "taken@x.com") { response { message } } }')
    assert result.errors[0].extensions["message_code"] == "PERMISSION_DENIED"
