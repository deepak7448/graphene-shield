import json

import pytest


def post(client, query, url="/graphql/"):
    return client.post(url, json.dumps({"query": query}), content_type="application/json")


@pytest.mark.django_db
def test_success_passthrough(client):
    response = post(client, "{ bookLists { totalCount } }")
    assert response.status_code == 200
    assert response.json() == {"data": {"bookLists": {"totalCount": 0}}}


@pytest.mark.django_db
def test_coded_error_envelope(client):
    response = post(client, "{ whoami }")
    assert response.status_code == 400
    body = response.json()
    assert body["message_code"] == "PERMISSION_DENIED"
    assert body["status"] is False
    assert body["errors"] == [body["message"]]


@pytest.mark.django_db
def test_unexpected_resolver_error_is_masked(client):
    body = post(client, "{ boom }").json()
    assert "hunter2" not in json.dumps(body)


@pytest.mark.django_db
def test_unexpected_resolver_error_exposed_when_configured(client, settings):
    settings.GRAPHENE_SHIELD = {"EXPOSE_EXCEPTION_MESSAGES": True}
    body = post(client, "{ boom }").json()
    assert body["message"] == "database password is hunter2"


@pytest.mark.django_db
def test_syntax_errors_keep_their_message(client):
    body = post(client, "{ bookLists ").json()
    assert "Syntax Error" in body["message"]


@pytest.mark.django_db
def test_multipart_upload_view(client):
    operations = json.dumps({"query": "{ bookLists { totalCount } }"})
    response = client.post("/upload/", {"operations": operations, "map": "{}"})
    assert response.status_code == 200
    assert response.json()["data"]["bookLists"]["totalCount"] == 0


@pytest.mark.django_db
def test_query_depth_is_limited(client, settings):
    deep = "{ bookLists { edges { node { name } } } }"
    assert post(client, deep).status_code == 200
    settings.GRAPHENE_SHIELD = {"MAX_QUERY_DEPTH": 2}
    from django.test import Client

    from graphene_shield.views import GraphQLView
    from tests.schema import schema

    view = GraphQLView(schema=schema)
    assert any("DepthLimitValidator" in getattr(r, "__name__", "") for r in view.validation_rules)
    # Standard validation still runs alongside the depth rule.
    body = (
        Client()
        .post(
            "/graphql/", json.dumps({"query": "{ noSuchField }"}), content_type="application/json"
        )
        .json()
    )
    assert "noSuchField" in body["message"]


@pytest.mark.django_db
def test_depth_limit_rejects_deep_operation(settings):
    import graphene

    from graphene_shield.views import GraphQLView

    class Node(graphene.ObjectType):
        child = graphene.Field(lambda: Node)
        name = graphene.String()

        def resolve_child(root, info):
            return {}

    class Q(graphene.ObjectType):
        root = graphene.Field(Node)

        def resolve_root(root, info):
            return {}

    settings.GRAPHENE_SHIELD = {"MAX_QUERY_DEPTH": 3}
    from django.test import RequestFactory

    view = GraphQLView.as_view(schema=graphene.Schema(query=Q))
    query = "{ root { child { child { child { child { name } } } } } }"
    request = RequestFactory().post(
        "/", json.dumps({"query": query}), content_type="application/json"
    )
    body = json.loads(view(request).content)
    assert "exceeds maximum operation depth" in body["message"]


@pytest.mark.django_db
def test_clean_responses_are_not_reparsed(client, monkeypatch):
    from types import SimpleNamespace

    import graphene_shield.views as views

    calls = []
    real = views.json
    # Watch only the envelope's own parsing, not graphene reading the request body.
    counting = SimpleNamespace(
        loads=lambda *a, **k: calls.append(1) or real.loads(*a, **k), dumps=real.dumps
    )
    monkeypatch.setattr(views, "json", counting)
    assert post(client, "{ bookLists { totalCount } }").status_code == 200
    assert calls == []
    assert post(client, "{ whoami }").status_code == 400  # errors are still enveloped
    assert calls == [1]
