"""GraphQL views that flatten errors into a single ``ResponseField``-shaped body.

When a request produces errors the body becomes::

    {"message": "...", "message_code": "...", "status": false,
     "field": null, "data": [...], "errors": ["<every error message>"]}

with HTTP 400. Successful responses are passed through unchanged.
"""

from __future__ import annotations

import json
import logging

from graphene.validation import depth_limit_validator
from graphene_django.views import GraphQLView as _GraphQLView
from graphql import GraphQLError, specified_rules

from graphene_shield.conf import shield_settings
from graphene_shield.responses import ResponseField

logger = logging.getLogger("graphene_shield.views")


class ResponseEnvelopeMixin:
    error_status_code = 400

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        max_depth = shield_settings.MAX_QUERY_DEPTH
        if max_depth:
            # graphql-core only applies its standard rules when none are given,
            # so they must be included explicitly alongside the depth limit.
            rules = list(self.validation_rules or specified_rules)
            self.validation_rules = [*rules, depth_limit_validator(max_depth)]

    def get_response(self, request, data, show_graphiql=False):
        result, status_code = super().get_response(request, data, show_graphiql)
        # Most responses have no errors: skip re-parsing their (possibly large) JSON.
        if result is None or getattr(self, "batch", False) or '"errors"' not in result:
            return result, status_code
        try:
            body = json.loads(result)
        except (TypeError, ValueError):
            logger.error("Could not parse GraphQL response body", exc_info=True)
            return json.dumps(
                ResponseField(message="Error parsing response", status=False).to_dict()
            ), 500

        errors = body.get("errors")
        if not errors:
            return result, status_code

        first = errors[0]
        envelope = ResponseField(
            message=first.get("message"),
            message_code=first.get("message_code"),
            status=first.get("status", False),
            field=first.get("field"),
            data=first.get("data") or [],
            errors=[e.get("message") for e in errors],
        )
        return json.dumps(envelope.to_dict()), self.error_status_code

    @staticmethod
    def format_error(error):
        if not isinstance(error, GraphQLError):
            error = GraphQLError(str(error), original_error=error)

        original = getattr(error, "original_error", None)
        message = error.message
        if original is not None and not isinstance(original, GraphQLError):
            # An unexpected exception escaped a resolver.
            logger.error("Unhandled resolver error: %s", original, exc_info=original)
            if not shield_settings.EXPOSE_EXCEPTION_MESSAGES:
                message = shield_settings.UNEXPECTED_ERROR_MESSAGE

        extensions = error.extensions or {}
        return {
            "message": message,
            "message_code": extensions.get("message_code"),
            "status": extensions.get("status", False),
            "data": extensions.get("data") or [],
        }


class GraphQLView(ResponseEnvelopeMixin, _GraphQLView):
    pass


try:
    from graphene_file_upload.django import FileUploadGraphQLView as _FileUploadGraphQLView
except ImportError:  # pragma: no cover - exercised only without the extra
    _FileUploadGraphQLView = None

if _FileUploadGraphQLView is not None:

    class FileUploadGraphQLView(ResponseEnvelopeMixin, _FileUploadGraphQLView):
        """Multipart-upload view (requires ``graphene-shield[upload]``)."""

    # Name used by projects migrating from in-house code.
    EnhancedFileUploadGraphQL = FileUploadGraphQLView
