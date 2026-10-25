from django.urls import path
from django.views.decorators.csrf import csrf_exempt

from graphene_shield.views import FileUploadGraphQLView, GraphQLView
from tests.schema import schema

urlpatterns = [
    path("graphql/", csrf_exempt(GraphQLView.as_view(schema=schema))),
    path("upload/", csrf_exempt(FileUploadGraphQLView.as_view(schema=schema))),
]
