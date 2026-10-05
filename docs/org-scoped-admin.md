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

The reusable scope mixin is unchanged. `OrganizationOwnerPermission`
is gone. An `OrganizationOwnership` row is the owner grant, and
startup registers it with no condition. Non-superusers therefore see
`Organization.objects.authorized(user, manage_organization)`, which is
the organizations they own. A team member with no ownership row is
outside that queryset. Superusers still bypass the scope. Conventional
organization create, rename, and delete call `gh_permissions.services`
instead of saving the row directly.

The hook plumbing is a private, domain-agnostic mixin. It is not a
general Core helper, and this note does not promote it to one.

`gh_permissions/_admin_scope.py` (`AuthorizedScopeAdminMixin`, 209
physical lines) owns that plumbing. It does not name a GH model and it
does not call Trusts. The application supplies
`authorization_scope_paths`, `get_authorized_scopes(request)`,
`scope_allows_add`, `scope_allows_change`, `scope_allows_delete`, and
`bypasses_scope` (superuser by default).

Proven only for this configuration:

- paths are `''` or a forward single-valued foreign key or one-to-one lookup;
- every path terminates at the same scope model, and multiple paths are AND;
- authorized scopes and protected objects use the same database;
- each relation that drives a scope path must have a registered scoped admin, otherwise related choices are not automatically filtered;
- stock admin routes only. Inlines, `list_editable`, raw-id fields, autocomplete fields, and any action other than `delete_selected` are rejected.

Many-to-many paths, reverse paths, multiple databases, dynamic inlines,
and custom forms are outside this contract. A second real consumer
should decide those.

`gh_permissions/admin.py` is the GH adapter. It resolves the
Organization-scoped `auth.Permission` `manage_organization`, returns
`Organization.objects.authorized(request.user, permission)`, declares
each model's path and add/change flags, and registers the concrete
admins. Scope checks stay on the mixin. Each concrete admin calls
the domain service for an authorization-bearing write instead of
`ModelAdmin.save_model`. Team members, the team ceiling, and a
collaborator's permissions are replaced from `save_related`, not
from the form's many-to-many save. `delete_queryset` walks the
selected rows through `delete_model`, including the built-in bulk
action. Team/repository alignment stays on
`TeamRepositoryPermission.clean`, which `ModelForm` calls and
`QuerySet.create` does not. The service checks that equality again
before it inserts or updates a grant. `Alias` is not registered
in admin.

`OrganizationOwnershipAdmin` sets `scope_allows_add`,
`scope_allows_change`, and `scope_allows_delete` to false, so a
non-superuser cannot edit the row that grants their authority.
Superusers still bypass that scope, and those writes call
`add_organization_owner`, `update_organization_ownership`, and
`delete_organization_ownership`. The example user admin is
`ServiceBackedUserAdmin`. No custom grant-management view was
added. The mixin is private. It is not a Core API.

## Authority

`Organization` has no `owner` column. `Operation` stays deleted.
`0003_organization_owner_permission` follows
`0002_auth_permission_terminal` and does not rewrite it.
`0004_shared_names_ownership_collaborators` removes
`OrganizationOwnerPermission`. Ownership is an
`OrganizationOwnership` row. `register_organization_owner` is
installed with no condition, reading `Organization.owner_group`
permissions for the organization and for that organization's
repositories. Each permission still has to match the object's content
type, so `manage_organization` does not grant on a repository and a
repository codename does not grant on the organization. A missing
`manage_organization` row authorizes nothing.
The seeded owner group holds the broad permissions, and the owner
registration reads them. Team does not.

With no object, `GhAuthorizationBackend.get_all_permissions` is empty,
so Django model permissions remain the coarse admin entrance.
`ModelBackend` supplies those no-object permissions and password
login. The runnable example settings list both backends, and the
seeded-owner proof does not replace `AUTHENTICATION_BACKENDS`:

```python
AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
    'django.contrib.auth.backends.ModelBackend',
)
```

With an object, `ModelBackend` contributes nothing and
`GhAuthorizationBackend` enumerates granted `auth.Permission`
codenames. Trusts supplies the organization boundary. A staff user
needs both layers. A model permission is not an organization grant.

## Can the mixin collapse?

No. Django admin still asks separate questions. The mixin hooks are
unchanged. The grant they read is now an `OrganizationOwnership` row.
The request matrix (guessed URLs, forged posts, foreign-key choices,
bulk delete, cascade, and the ownership-flag cases) is adapted to
`OrganizationOwnership` and `RepositoryCollaborator` and runs in
`tests/test_org_scoped_admin.py`.

The hooks stay for the same reasons as before:

| Hook | Why it stays |
| --- | --- |
| `get_queryset` | Non-superusers are filtered to `Organization.objects.authorized`. That queryset is the organizations where the user has an ownership row. |
| `has_add_permission` | Add has no object. `scope_allows_add` is false on `OrganizationAdmin` and on `OrganizationOwnershipAdmin`. |
| `has_change_permission` / `has_delete_permission` | The flags are consulted even when no object is passed. Object deletes still require `_in_scope`. |
| `formfield_for_foreignkey` / `formfield_for_manytomany` | Related choices come from the related admin's scoped queryset when that admin is scoped. `RepositoryCollaborator.permissions` points at `auth.Permission`, which is not scoped. |
| `save_model` | Concrete admins check the same add/change flags and `_in_scope`, then call the domain service. The mixin method remains the backstop for a class that does not override it. |
| `get_actions` / `check` | Inlines, `list_editable`, raw-id fields, autocomplete fields, and any action other than `delete_selected` fail closed (`admin_scope.E001`, `admin_scope.E002`). |

`User.objects.permitted` and `get_permitted_users` are not used here.
There is no user-listing callsite in the scope hooks.

## Known boundary

Staff owners administer the organizations where they have an ownership
row. Superusers bypass that filter. A team member with no ownership
row does not. A misaligned team grant is still rejected by
`TeamRepositoryPermission.clean` on a `ModelForm`. `QuerySet.create`
does not call `clean`.

Host projects that turn the admin on, including this runnable example,
must include `ModelBackend` beside `GhAuthorizationBackend`. Forged
foreign parents are rejected by the scoped form queryset before a row
is written. An organization that is a legal choice for the actor, but
is not the stored `Team.organization` or `Repository.organization`,
is refused by `move_team_organization` or
`move_repository_organization` before the name or the many-to-many
sets change.

## Relationship writes

Authorization-bearing admin edits call `gh_permissions.services`.
The service locks the persisted organization and authorizes
`manage_organization` before it writes. The admin does not insert
the row, or replace a many-to-many, before that call. A refusal
rolls back the change-form or delete transaction and is shown as a
validation error or a message.

`OrganizationOwnershipAdmin` still sets `scope_allows_add`,
`scope_allows_change`, and `scope_allows_delete` to false, so a
non-superuser cannot edit an ownership row here. Superusers still
bypass the scope mixin. Their add, change, delete, and bulk delete
call the ownership services. Removing the last owner of a surviving
conventional organization raises `LastOrganizationOwner`. The admin
shows that message and leaves the row in place.

`delete_user` does the same when the user is the sole owner of any
surviving conventional organization. User admin and deleting that
user's personal organization both call `delete_user`. The personal
organization is not itself a reason to refuse, because it would be
deleted with the user.

`manage_organization` on a stored organization is the management
inquiry. `read_repository`, `write_repository`, and
`admin_repository` are the repository grants being edited. A
misaligned team grant is still rejected by the model form, and the
delete service refuses it as well, including for a superuser.
Deleting the team calls `delete_team`. Creating a repository calls
`create_repository`. Deleting a repository calls `delete_repository`,
which locks the stored organization and requires `manage_organization`
before the row and its collaborator and team-grant cascade are
removed. A raw queryset write still does not call the services.
