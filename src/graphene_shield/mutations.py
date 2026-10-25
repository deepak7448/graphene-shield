from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import graphene
from django.db import transaction
from graphql import GraphQLError

from graphene_shield import files
from graphene_shield.authentication import authenticate
from graphene_shield.conf import shield_settings
from graphene_shield.responses import ResponseField
from graphene_shield.utils import is_effectively_empty, sanitize_input

logger = logging.getLogger("graphene_shield.mutations")


class BaseMutation(graphene.Mutation):
    """Mutation base with a uniform ``response: [ResponseField]`` result.

    Lifecycle of ``mutate``:

    1. if ``login_required``, the request is authenticated (``AUTHENTICATOR``) first;
    2. string inputs are stripped (``SANITIZE_INPUT``);
    3. ``validate_input(info, data)`` — return a ``ResponseField`` or a list of
       them to short-circuit with errors;
    4. ``perform_mutation(root, info, **data)`` — your logic; must return ``cls(...)``.
       With ``atomic = True`` (default) it runs in a transaction, so an exception
       rolls back everything it wrote;
    5. unexpected exceptions are logged and turned into a failed response.

    ``GraphQLError`` (including ``EnhancedGraphQLError``, raised by the JWT
    guards) propagates so it reaches the top-level ``errors`` array.

    Example::

        class PauseSchool(BaseMutation):
            school = graphene.Field(SchoolType)

            class Arguments:
                id = graphene.ID(required=True)

            @classmethod
            def perform_mutation(cls, root, info, id):
                school = School.objects.get(pk=resolve_global_id(id))
                school.pause()
                return cls(school=school, response=[cls.success("School paused.")])
    """

    response = graphene.List(ResponseField, description="Mutation response details.")

    # Authenticate with the ``AUTHENTICATOR`` setting (JWT by default) before
    # input is validated, so anonymous callers learn nothing from validation errors.
    login_required: bool = False
    # Run ``perform_mutation`` in ``transaction.atomic()``.
    atomic: bool = True

    class Meta:
        abstract = True

    # ── Response builders ──

    @classmethod
    def add_response(
        cls,
        message: str,
        message_code: str | None = None,
        status: bool | None = None,
        field: str | None = None,
        data: list | None = None,
        errors: list | None = None,
    ) -> ResponseField:
        return ResponseField(
            message=message,
            message_code=message_code,
            status=status,
            field=field,
            data=data,
            errors=errors,
        )

    @classmethod
    def success(cls, message: str, message_code: str = "SUCCESS", data: list | None = None):
        return cls.add_response(message=message, message_code=message_code, status=True, data=data)

    @classmethod
    def failure(cls, error: Any, field: str | None = None, data: list | None = None):
        """Failed response from a string or any object exposing ``.message``/``.code``."""
        if isinstance(error, str):
            message, code = error, None
        else:
            message, code = error.message, error.code
        return cls.add_response(
            message=message, message_code=code, status=False, field=field, data=data
        )

    @classmethod
    def add_field_error(cls, field_name: str, error_enum: Any, index: int | None = None):
        """Field-scoped failure; ``index`` marks the position inside a list input."""
        response = cls.failure(error_enum, field=field_name)
        if index is not None:
            response.message = f"{response.message} at {index}"
        return response

    # ── Validation helpers ──

    is_effectively_empty = staticmethod(is_effectively_empty)

    @classmethod
    def _validate_required_fields(
        cls,
        data_dict: dict,
        field_map: dict[str, Any],
        errors: list,
        prefix: str = "",
    ) -> None:
        """Append a field error for every ``field_map`` key whose value is effectively empty."""
        for field, error_enum in field_map.items():
            if is_effectively_empty(data_dict.get(field)):
                errors.append(cls.add_field_error(f"{prefix}{field}", error_enum))

    @classmethod
    def _validate_file(
        cls,
        file,
        field_name: str,
        errors: list,
        idx: int | None = None,
        allowed_types: Iterable[str] | None = None,
        max_file_size: int | None = None,
        check_file_size: bool = True,
        error_messages: Any = None,
        validate_content: bool = True,
    ) -> None:
        """Validate an upload and append a field error if it fails. See ``files.validate_file``."""
        error = files.validate_file(
            file,
            allowed_types=allowed_types,
            max_file_size=max_file_size,
            check_file_size=check_file_size,
            validate_content=validate_content,
            error_messages=error_messages,
        )
        if error is not None:
            errors.append(cls.add_field_error(field_name, error, idx))

    _detect_file_type = staticmethod(files.detect_file_type)
    _contains_pdf_active_content = staticmethod(files.contains_pdf_active_content)

    # ── Hooks ──

    @classmethod
    def validate_input(cls, info, data: dict) -> ResponseField | list[ResponseField] | None:
        return None

    @classmethod
    def perform_mutation(cls, root, info, **data) -> BaseMutation:
        raise NotImplementedError(f"{cls.__name__} must implement perform_mutation().")

    @classmethod
    def handle_exception(cls, exc: Exception) -> BaseMutation:
        logger.error("%s failed: %s", cls.__name__, exc, exc_info=True)
        if shield_settings.EXPOSE_EXCEPTION_MESSAGES:
            message = str(exc)
        else:
            message = shield_settings.UNEXPECTED_ERROR_MESSAGE
        return cls(
            response=[
                cls.add_response(
                    message=message,
                    message_code=shield_settings.UNEXPECTED_ERROR_CODE,
                    status=False,
                )
            ]
        )

    @classmethod
    def mutate(cls, root, info, **data) -> BaseMutation:
        if cls.login_required:
            authenticate(info.context)

        if data and shield_settings.SANITIZE_INPUT:
            data = sanitize_input(data, unescape_html=shield_settings.UNESCAPE_HTML_INPUT)

        try:
            validation_errors = cls.validate_input(info, data)
            if validation_errors:
                if not isinstance(validation_errors, list):
                    validation_errors = [validation_errors]
                return cls(response=validation_errors)

            if cls.atomic:
                with transaction.atomic():
                    result = cls.perform_mutation(root, info, **data)
            else:
                result = cls.perform_mutation(root, info, **data)
        except (GraphQLError, NotImplementedError):
            raise
        except Exception as exc:
            return cls.handle_exception(exc)

        if result is None:
            raise TypeError(f"{cls.__name__}.perform_mutation() returned None; return cls(...).")
        return result
