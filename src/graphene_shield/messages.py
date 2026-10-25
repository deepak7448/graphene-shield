"""Default error messages.

Any object exposing ``.message`` and ``.code`` works wherever the package takes
an error (``BaseMutation.add_field_error``, ``validate_file``...), so you can
pass your own enums. Subclass these containers to change wording::

    class MyFileErrors(FileErrorMessages):
        FILE_TOO_LARGE = ErrorMessage("Max 5 MB.", "FILE_TOO_LARGE")
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorMessage:
    message: str
    code: str


class CommonErrorMessages:
    REQUIRED = ErrorMessage("This field is required.", "REQUIRED")
    BAD_REQUEST = ErrorMessage("Bad request", "BAD_REQUEST")
    INVALID_SORT_FIELD = ErrorMessage("Invalid sort field.", "INVALID_SORT_FIELD")
    PAGINATION_LIMIT_EXCEEDED = ErrorMessage(
        "Requested page size exceeds the allowed limit.", "PAGINATION_LIMIT_EXCEEDED"
    )
    PAGINATION_REQUIRED = ErrorMessage(
        "You must provide `first` or `last` to paginate this connection.", "PAGINATION_REQUIRED"
    )
    PAGINATION_CONFLICT = ErrorMessage(
        "`offset` cannot be combined with `before`.", "PAGINATION_CONFLICT"
    )


class FileErrorMessages:
    FILE_REQUIRED = ErrorMessage("File is required.", "FILE_REQUIRED")
    FILE_TOO_LARGE = ErrorMessage("File exceeds the maximum allowed size.", "FILE_TOO_LARGE")
    INVALID_FILE_TYPE = ErrorMessage("File type is not allowed.", "INVALID_FILE_TYPE")
    ACTIVE_CONTENT_DETECTED = ErrorMessage(
        "PDF contains active content (JavaScript, actions or embedded files).",
        "ACTIVE_CONTENT_DETECTED",
    )


class AuthErrorMessages:
    PERMISSION_DENIED = ErrorMessage(
        "Authentication credentials were not provided.", "PERMISSION_DENIED"
    )
    USER_INACTIVE = ErrorMessage("User account is disabled.", "USER_INACTIVE")


class JwtErrorMessages(AuthErrorMessages):
    INVALID_FORMAT = ErrorMessage(
        "Authorization header must be '<prefix> <token>' with an accepted prefix.",
        "INVALID_FORMAT",
    )
    TOKEN_EXPIRED = ErrorMessage("Token has expired.", "TOKEN_EXPIRED")
    TOKEN_REVOKED = ErrorMessage("Token has been revoked.", "TOKEN_REVOKED")
    WRONG_TOKEN_TYPE = ErrorMessage("This kind of token can't be used here.", "WRONG_TOKEN_TYPE")
    TOKEN_INVALID = ErrorMessage("Token is invalid.", "TOKEN_INVALID")
    INVALID_SIGNATURE = ErrorMessage("Token signature is invalid.", "INVALID_SIGNATURE")
    INVALID_ALGORITHM = ErrorMessage("Token algorithm is not allowed.", "INVALID_ALGORITHM")
    INVALID_AUDIENCE = ErrorMessage("Token audience is invalid.", "INVALID_AUDIENCE")
    INVALID_ISSUER = ErrorMessage("Token issuer is invalid.", "INVALID_ISSUER")
    PAYLOAD_INVALID = ErrorMessage("Token payload is invalid.", "PAYLOAD_INVALID")
    USER_NOT_FOUND = ErrorMessage("User not found.", "USER_NOT_FOUND")
