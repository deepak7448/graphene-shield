from __future__ import annotations

from typing import Any

import graphene
from graphql import GraphQLError


class ResponseField(graphene.ObjectType):
    """Standard result entry returned in a mutation's ``response`` list."""

    message = graphene.String(description="Human-readable message.")
    message_code = graphene.String(description="Machine-readable message code.")
    status = graphene.Boolean(description="True on success, False on failure.")
    field = graphene.String(description="Input field the message refers to, if any.")
    data = graphene.List(graphene.String, description="Extra data attached to the message.")
    errors = graphene.List(graphene.String, description="List of error strings.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "message_code": self.message_code,
            "status": self.status,
            "field": self.field,
            "data": self.data or [],
            "errors": self.errors or [],
        }


class EnhancedGraphQLError(GraphQLError):
    """``GraphQLError`` carrying ``message_code``, ``status`` and ``data`` in ``extensions``."""

    def __init__(
        self,
        message: str,
        message_code: str | None = None,
        status: bool | None = None,
        data: Any = None,
        extensions: dict[str, Any] | None = None,
    ):
        extensions = dict(extensions or {})
        extensions["message_code"] = message_code
        extensions["status"] = status
        extensions["data"] = data
        super().__init__(message, extensions=extensions)

    @classmethod
    def from_message(cls, error: Any, data: Any = None) -> EnhancedGraphQLError:
        """Build from any object exposing ``.message`` and ``.code``."""
        return cls(message=error.message, message_code=error.code, status=False, data=data or [])
