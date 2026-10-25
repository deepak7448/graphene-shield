from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser

from tests.schema import schema


class FakeRequest(SimpleNamespace):
    def __init__(self, token=None, header=None):
        meta = {}
        if header is not None:
            meta["HTTP_AUTHORIZATION"] = header
        elif token is not None:
            meta["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        super().__init__(META=meta, headers={}, user=AnonymousUser())


@pytest.fixture
def execute():
    def _execute(query, variables=None, request=None):
        return schema.execute(
            query, variable_values=variables, context_value=request or FakeRequest()
        )

    return _execute
