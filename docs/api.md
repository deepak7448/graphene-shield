# API reference

## Mutations: `graphene_shield.mutations`

`BaseMutation`. Subclass it and add `class Arguments` and output fields as usual. Every subclass gets a `response: [ResponseField]` field.

| Member | Description |
|---|---|
| `login_required = False` | Authenticate with `AUTHENTICATOR` before anything else runs. |
| `atomic = True` | Run `perform_mutation` in `transaction.atomic()`. |
| `validate_input(info, data)` | Return a `ResponseField` or a list of them to stop with errors; `None`/`[]` to continue. |
| `perform_mutation(root, info, **data)` | Your logic. Must return `cls(...)`. |
| `success(message, message_code="SUCCESS", data=None)` | Successful `ResponseField`. |
| `failure(error, field=None, data=None)` | Failed `ResponseField` from a string or any object with `.message`/`.code`. |
| `add_field_error(field_name, error, index=None)` | Field-scoped failure; `index` adds "at N" for list inputs. |
| `add_response(message, message_code, status, field, data, errors)` | Low-level builder. |
| `_validate_required_fields(data, field_map, errors, prefix="")` | Append an error for each empty field (`None`, blank, empty list/dict). |
| `_validate_file(file, field_name, errors, idx=None, allowed_types=None, max_file_size=None, ...)` | `validate_file` that appends a field error. |
| `handle_exception(exc)` | Override to change how unexpected exceptions become responses. |

## Lists: `graphene_shield.queries`, `.connections`, `.filters`, `.sorting`

| Name | Description |
|---|---|
| `ModelListField(node_type, filter_input=None, search_fields=(), sort_input=SortingInput, distinct=False, pagination=None, max_limit=..., on=None, enforce_first_or_last=False)` | List field that applies filters, search, sort and pagination. |
| `FilterConnectionField(node_type, pagination=None, max_limit=..., on=None, enforce_first_or_last=False)` | The same, without filters, search and sort. |
| `CountableDjangoObjectType` | `DjangoObjectType` with a `totalCount` connection and a `number` (primary key) field. |
| `IdFilterInput`, `BaseFilterInput` | Filter inputs. Subclass them and override `filter_queryset(queryset, filters)`; `common_field_map` renames columns. |
| `make_sort_field_enum(name, {"KEY": ["field", "pk"]})` | Sort-field enum; every listed field is used in order. |
| `make_sorting_input(name, enum)`, `SortingInput`, `OrderDirection` | Sort input types. `SortingInput` offers `ID`, `CREATED_AT`, `UPDATED_AT`, `NAME`. |
| `resolve_model_list(queryset, *, filter_input, filters, search, search_fields, sort_by, distinct)` | Filter, search and sort a queryset yourself. |

## Files: `graphene_shield.files`

| Name | Description |
|---|---|
| `validate_file(file, allowed_types=None, max_file_size=None, check_file_size=True, validate_content=True, error_messages=None)` | Returns the failing error message, or `None` if valid. |
| `detect_file_type(file)` | `("PDF", "application/pdf")`, or `(None, mime)` when unknown or inconsistent. |
| `contains_pdf_active_content(file)` | `True` for active content, or for files that can't be inspected. |
| `DEFAULT_MAX_FILE_SIZE`, `DEFAULT_ALLOWED_TYPES` | 10 MB and `{"PDF"}`. |
| `MAX_PDF_INFLATED_BYTES`, `MAX_PDF_OBJECTS` | Safety limits for the PDF check: 64 MB inflated, 500,000 objects. |

## Authentication: `graphene_shield.authentication`, `graphene_shield.auth`

| Name | Description |
|---|---|
| `authentication.login_required` | Resolver decorator using `AUTHENTICATOR`. |
| `authentication.authenticate(request, authenticator=None)` | Authenticate, run `USER_VALIDATOR`, set `request.user`; cached per request. |
| `authentication.session_authenticate(request)` | Authenticator for Django sessions or any middleware that sets `request.user`. |
| `auth.jwt_required` | Resolver decorator that always uses JWT (`jwt` extra). |
| `auth.authenticate_request(request)` | The default JWT authenticator. |
| `auth.decode_token(token)`, `auth.get_token_from_request(request)` | Lower-level JWT helpers raising coded errors. |

## Views, responses and helpers

| Name | Description |
|---|---|
| `views.GraphQLView`, `views.FileUploadGraphQLView` | Views with the error envelope and depth limit; the second one handles multipart uploads (`upload` extra). |
| `responses.ResponseField` | `message`, `messageCode`, `status`, `field`, `data`, `errors`. |
| `responses.EnhancedGraphQLError(message, message_code=None, status=None, data=None)` | A `GraphQLError` carrying a code; `.from_message(error)` builds one from an `ErrorMessage`. |
| `messages.ErrorMessage(message, code)` | Error definition. The defaults are in `CommonErrorMessages`, `FileErrorMessages`, `AuthErrorMessages` and `JwtErrorMessages`. |
| `utils.resolve_global_id(global_id, expected_type=None)` | Decode a Relay ID, raising `ValueError` if it is invalid. |
| `utils.sanitize_input(value, *, unescape_html=False)`, `utils.is_effectively_empty(value)` | The input helpers `BaseMutation` uses. |

The package logs through the standard `logging` module under the `graphene_shield.mutations`, `graphene_shield.views`, `graphene_shield.queries` and `graphene_shield.files` loggers.
