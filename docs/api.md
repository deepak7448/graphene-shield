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
| `NodeType.prefetch_for_selection(queryset, node_fields, info)` | Optional classmethod on the node type. Called once per page with the row fields the client selected (`{name: [sub-selections]}`); return the queryset with `prefetch_related(...)` added. Not called when no row fields are selected or for combined (`.union()`) querysets. |

## Selections and relations: `graphene_shield.selection`, `graphene_shield.relations`

| Name | Description |
|---|---|
| `selection.collect_selected_fields(info, selection_sets)` | `{field name: [sub-selection sets]}` for the given selection sets. Expands fragments, honours `@skip`/`@include`, ignores aliases. |
| `selection.connection_node_selection(info)` | `(connection_fields, node_fields)` for the list field being resolved; `node_fields` come from `edges { node }` or `items`. |
| `selection.nested_node_fields(info, selection_sets)` | Row fields of a nested connection, given that field's sub-selections (e.g. `node_fields["reviews"]`). |
| `relations.NonCascadingGenericRelation` | `GenericRelation` for querying and prefetching only: deleting the parent leaves the generic children alone. No column, no migration. Needs `django.contrib.contenttypes`. |

## Relation prefetching: `graphene_shield.prefetch`

| Name | Description |
|---|---|
| `relation_prefetch(model, field_name, info, *, through=None, to_attr=None)` | `Prefetch` of a foreign key or one-to-one (either side). Goes through the target type's `get_queryset` over `Model.objects` when the type overrides it (as `get_node` does), else the base manager (as attribute access does). Stored in `prefetched_<field_name>` by default. `through` prefixes the lookup path. Raises `ValueError` for to-many relations. |
| `relation_resolver(field_name, *, to_attr=None)` | Resolver reading that prefetch; without it, does exactly what graphene-django's resolver does. Assign in the type body: `resolve_author = relation_resolver("author")`. |
| `SelectionPrefetcher(model, {graphql_field: builder})` | Prefetches chosen by the selected row fields, in mapping order. Builders: lookup string, `Prefetch`, `related(...)`, `nested(...)`, callable `(selection, info) -> lookups`, or a list of these. Callable as `(queryset, node_fields, info)`, so assign it as `prefetch_for_selection`. `.prefetches(node_fields, info)` returns the lookups. |
| `related(field_name, *, to_attr=None)` | Builder: `relation_prefetch` of the prefetcher's model. |
| `nested(relation, prefetcher, *, connection=False)` | Builder: follow `relation`, then run `prefetcher` for the fields selected beneath it. A to-one relation is loaded with `relation_prefetch` (resolve it with `relation_resolver`) and inner lookups run through its `to_attr`; a to-many relation is a plain lookup. `connection=True` reads row fields from `edges { node }`. |
| `under_path(prefix, lookup)` | The same lookup one relation deeper; a `Prefetch` keeps its queryset and `to_attr`. |
| `registered_type(model)` | The type in graphene-django's global registry for `model`. |

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
