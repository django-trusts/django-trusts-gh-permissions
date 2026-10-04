# Organization-scoped admin proof

Phase 0 asks whether one small reusable admin layer can make Django's
built-in admin tenant-safe for organization-owner grant management.
This note is the result after rebasing onto GH `dev`
`d2c9e63f0c7461bd7ffbeeff7513fc54c1cf323c`.

This bounded example does not reproduce GitHub directly. Its `Team` is
role-like: it groups members, carries an allowed-operation ceiling, and
receives repository grants. It does not model the broader collaboration
behavior of a real GitHub team.

## Result

The proof succeeded, and the reusable part is no longer GH-specific.

`gh_permissions/_admin_scope.py` (`AuthorizedScopeAdminMixin`, 190
physical lines) owns stock-admin scope plumbing. It does not name a GH
model and it does not call Trusts. The application contract is
`authorization_scope_paths`, `get_authorized_scopes(request)`,
`scope_allows_add`, `scope_allows_change`, and `bypasses_scope`
(superuser by default).

`gh_permissions/admin.py` (83 physical lines) is the GH adapter. It
resolves the Organization-scoped `auth.Permission` `manage_organization`,
returns `Organization.objects.authorized(request.user, permission)`,
declares each model's path and add/change flags, and registers the
concrete admins. Team/repository alignment stays on
`TeamRepositoryPermission.clean`, which `ModelForm` calls and
`QuerySet.create` does not.

Concrete admins do not override the security methods. No custom
grant-management view was added. The mixin is private. It is not a
Core API.

## Authority

`Organization` has no `owner` column. `Operation` stays deleted.
`0003_organization_owner_permission` follows
`0002_auth_permission_terminal` and does not rewrite it. It adds
`manage_organization` and `OrganizationOwnerPermission` (`organization`,
`owner`, `operation` → `auth.Permission`). No grant row means unowned.

`register_organization_owner` is a third root with
`content='organization'`. It does not authorize repository operations.
Deleting the grant, retargeting `owner`, or pointing `operation` at
another permission drops admin access on the next request. A missing
`manage_organization` row authorizes nothing.

With no object, `GhAuthorizationBackend.get_all_permissions` is empty,
so Django model permissions remain the coarse admin entrance and the
harness still lists `ModelBackend` for those and for `get_user`. With
an object, the backend enumerates granted `auth.Permission` codenames.
Trusts supplies the organization boundary. A staff user needs both
layers.

## Can the mixin collapse?

No. Django admin still asks separate questions. What did get smaller
is the GH adapter: it no longer contains the hook matrix.

Removed after the hostile suite stayed green:

- dynamic `get_form()`. Scoped foreign-key querysets already reject an
  out-of-scope choice (`test_forged_add_and_change_posts_cannot_name_another_organization`
  still expects the invalid-choice response). `save_model` still
  rejects an instance that bypassed the form
  (`test_save_model_rejects_an_out_of_scope_instance`). A 403 from that
  backstop is enough.
- `has_view_permission` scope check. Stock `get_object()` loads through
  `get_queryset()`. A guessed foreign URL never returns the row, so
  the view redirects to the admin index
  (`test_guessed_cross_org_change_history_and_delete_urls_fail_closed`).

Retained, with the path that fails if the hook is removed:

| Hook | Why it stays |
| --- | --- |
| `get_queryset` | `test_owner_sees_only_owned_rows_on_each_changelist` and `test_owner_changelist_is_sql_filtered_before_pagination`. The changelist SQL includes the grant table and `LIMIT`. |
| `has_add_permission` | `test_staff_with_permissions_and_no_owned_organization_mutates_nothing` and the organization-add 403 in `test_owner_cannot_mint_or_retarget_the_authority_grant`. Add has no object. |
| `has_change_permission` | `scope_allows_change` is false on the authority grant. Stock `has_change_permission` ignores the object. `_changeform_view` then returns 200 instead of 403 (`test_owner_cannot_mint_or_retarget_the_authority_grant`, `AssertionError: 200 != 403`). |
| `has_delete_permission` | `django.contrib.admin.utils.get_deleted_objects` calls `has_delete_permission(request, obj)` on each collected related object, not only rows from the parent's queryset. Without the scope check, `test_misaligned_grant_blocks_owner_cascade_until_superuser_removes_it` no longer sees the protected grant. `scope_allows_change` false also denies deleting the authority grant. |
| `formfield_for_foreignkey` | `test_foreign_key_choices_are_limited_to_owned_rows`. Choices come from the related admin's scoped `get_queryset`. |
| `formfield_for_manytomany` | Same related-queryset call. No current GH model has a many-to-many to a scoped model, so the behavioral suite does not fail if this method is deleted. It stays so that hook cannot silently use the unscoped stock queryset. |
| `save_model` | Called after `save_form(commit=False)`. `test_save_model_rejects_an_out_of_scope_instance` posts a foreign team straight to `save_model` and requires `PermissionDenied`. |
| `get_actions` / `check` | Inlines, `list_editable`, raw-id fields, autocomplete fields, and any action other than `delete_selected` fail closed (`admin_scope.E001`, `admin_scope.E002`, and `get_actions`). `test_unsupported_surfaces_fail_checks`. |

`User.objects.permitted` and `get_permitted_users` are not used here.
There is no user-listing callsite in the scope hooks.

## Hostile matrix

Real admin requests still cover guessed URLs, forged POSTs, foreign-key
choices, bulk delete, cascade, staff with no grant, ordinary users, and
superusers. Added proofs: no grant row, then adding one; deleting it,
retargeting its owner, or changing its permission; team membership or a
repository permission never opens the admin; a missing
`manage_organization` permission authorizes nothing;
`Organization.objects.authorized` agrees with the owner changelist.

## Known boundary

A misaligned team grant is hidden from the owner, and cascade delete of
the parent team stays blocked until a superuser removes the grant.
Aligned grants cascade inside the organization.

Host projects that turn the admin on must include `ModelBackend` beside
`GhAuthorizationBackend`.
