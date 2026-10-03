# Organization-scoped admin proof

Phase 0 asks whether one small reusable `OrgScopedAdmin` layer can make
Django's built-in admin tenant-safe for organization-owner grant
management. This note is the result.

This bounded example does not reproduce GitHub directly. Its `Team` is
role-like: it groups members, carries an allowed-operation ceiling, and
receives repository grants. It does not model the broader collaboration
behavior of a real GitHub team.

## Result

The proof succeeded. Organization owners administer grants through
stock `ModelAdmin` forms, templates, and routes. One mixin
(`gh_permissions.admin.OrgScopedAdmin`) plus a declarative
`organization_path` on each model supplies the tenant boundary.
Concrete admin classes do not override the security methods. No custom
grant-management view, no parallel admin framework, and no generic
admin code was added to Core.

`Operation` stays a bare `admin.site.register(Operation)` global
catalog. Application superusers are unrestricted on every registered
model. Organization owners are not given Django permissions on users
or operations, so user-account and operation-catalog pages stay
outside their authority. User and operation foreign keys remain global
choices on grant and membership forms.

## Owner relation

`Organization.owner` is a nullable foreign key to `AUTH_USER_MODEL`
with `on_delete=SET_NULL`. That is one owner per organization.
Existing rows stay unowned (`NULL`) across `0002_organization_owner`.
Unowned organizations are visible only to superusers. Deleting the
owning user clears `owner` and leaves the organization in place.

Ownership is administration authority. It is not a Trusts grant.
Team membership remains only an authorization path.

Non-superusers cannot add organizations and cannot change `owner`, so
a row cannot be moved into or out of another owner's scope. A team or
repository can move only to an organization that same user owns.

## Mixin

For a non-superuser the mixin:

- filters `get_queryset`, which is what changelist pagination, change,
  history, delete, and `delete_selected` all read
- denies object view, change, and delete when any declared
  organization is not owned by that user
- denies add when the user owns nothing, and denies organization add
  entirely
- limits foreign-key and many-to-many choices to related models that
  are themselves `OrgScopedAdmin` (users and operations stay global)
- locks `Organization.owner` and restores it on save
- rejects a team repository grant whose team and repository are in
  different organizations
- re-checks scope and alignment in `save_model`

Declared paths:

| Model | `organization_path` |
| --- | --- |
| `Organization` | `''` (the row is the organization) |
| `Team`, `Repository` | `organization` |
| `UserRepositoryPermission` | `repository__organization` |
| `TeamRepositoryPermission` | `team__organization` and `repository__organization` |

`TeamRepositoryPermission.alignment_paths` requires those two
organizations to be the same one. That matches the existing Trusts
condition. It does not change how the condition is evaluated.

## Hostile matrix

Real admin requests cover:

- superuser access to owned, foreign, and unowned organizations
- owner changelists showing owned rows only, with the owner filter in
  the same SQL statement as `LIMIT`
- guessed cross-organization change, history, and delete URLs
- forged add and change POSTs, including `_saveasnew` and `_to_field`
- foreign-key choices on add and change
- team-member choices, a forged member id, and a real user from
  another organization (allowed as a grant target, not as an account
  edit)
- direct and team grant create, update, and revoke
- `delete_selected`, including a foreign primary key and `select_across`
- direct delete and in-organization cascade
- a corrupt cross-organization grant, which blocks owner cascade
  delete until a superuser removes it
- ownership changes: owners cannot retarget `owner`; a superuser can
- staff with model permissions and no owned organization
- a non-staff user and an ordinary team member
- superuser preservation, including user and operation admin

A filtered changelist is not the only check.

## Authorization preserved

Admin writes do not bypass the registry. Direct and team paths still
OR. Membership alone and an over-ceiling team grant still deny.
Organization alignment still denies. The owner, with no membership and
no direct grant, still has no repository permission. `AUTH_USER_MODEL`
is unchanged. Zero stays absent. Existing fixed-query tests still
apply; an admin-created allow is still one query.

## Known boundary

If a team repository grant is misaligned (team organization and
repository organization differ), the owner cannot see that grant and
Django refuses to cascade-delete the parent team, because deleting the
grant would require delete permission the owner does not have. A
superuser deletes the corrupt grant. Ordinary aligned grants cascade
inside the organization. This stays inside the mixin. It did not
require a custom view.

Host projects that turn the admin on must include
`django.contrib.auth.backends.ModelBackend` beside
`GhAuthorizationBackend`. The GH backend still returns no Django
permission strings and does not load session users. `ModelBackend` is
what makes staff model permissions and admin login work.
