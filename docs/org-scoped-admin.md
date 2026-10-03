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

Django model permissions remain the coarse admin entrance.
`ModelBackend` may supply those staff permissions.
`GhAuthorizationBackend` still returns no Django permission strings.
Trusts supplies the organization boundary. A staff user needs both.

## Can the mixin be much smaller?

No. It should not collapse, and it did not.

Django admin does not ask one question. These hooks are separate, and
a queryset filter alone is not acceptance:

- `get_queryset` (changelist, pagination, history, delete, bulk delete)
- `has_view_permission`, `has_change_permission`, `has_delete_permission`
- `has_add_permission`
- `formfield_for_foreignkey` and `formfield_for_manytomany`
- `form.clean`
- `save_model`

What did get smaller is the authority path inside those hooks. At
`bb2cf037` the mixin also filtered with `owner=user` and compared
`owner_id == user.pk` (`_scope_queryset`). That second ORM path is
gone. Every hook now calls one scope:
`Organization.objects.authorized(user, management_operation)`.
Related-choice querysets call the related admin's `get_queryset`,
which uses that same scope. There is no second copy of the hook
matrix.

Counted the same way (the `class OrgScopedAdmin` body, from the class
statement through `save_model`), the mixin is 161 lines. `bb2cf037`
was 166. The revised class is smaller, not larger.

## Owner relation

`OrganizationOwnerPermission` is the administration grant: one-to-one
`organization`, `owner` to `AUTH_USER_MODEL`, and `operation` to
`Operation`. No row means unowned. `register_organization_owner`
registers it on its own, with `content='organization'`, after the
direct and team repository roots. Owning an organization does not
authorize repository operations.

`Organization` has no `owner` column. `Organization.objects` is the
stock `AuthorizedManager`. The mixin resolves one declared code,
`manage`. A missing `Operation` authorizes nothing. Deleting the grant,
retargeting `owner`, or pointing `operation` at a different row drops
admin access on the next request, because `authorized()` no longer
returns that organization.

Unowned organizations are visible only to superusers. Superuser bypass
stays on the admin class. `AuthorizedQuerySet` does not treat a
superuser as a grant, so `authorized()` and the owner changelist agree
for a non-superuser, and the changelist SQL filters through the grant
table before `LIMIT`.

The owner cannot add, change, or delete `OrganizationOwnerPermission`,
including a forged POST and `delete_selected`. A superuser assigns and
retargets that row. Deleting the user deletes the grant and leaves the
organization, its teams, and its repositories in place.

Non-superusers cannot add organizations. A team or repository can move
only to an organization that same user manages.

## Mixin

For a non-superuser the mixin:

- filters `get_queryset` with the authorized organization queryset,
  which is what changelist pagination, change, history, delete, and
  `delete_selected` all read
- denies object view when any declared organization is absent from
  that queryset
- denies object change and delete on that same check, and also when
  `owners_may_change` is false
- denies add when the user manages nothing, and denies organization
  add and authority-grant writes entirely
- limits foreign-key and many-to-many choices to the related
  `OrgScopedAdmin.get_queryset` (users and operations stay global)
- rejects a team repository grant whose team and repository are in
  different organizations
- re-checks the same write rule and alignment in `save_model`

Declared paths:

| Model | `organization_path` |
| --- | --- |
| `Organization` | `''` (the row is the organization) |
| `Team`, `Repository` | `organization` |
| `UserRepositoryPermission` | `repository__organization` |
| `TeamRepositoryPermission` | `team__organization` and `repository__organization` |
| `OrganizationOwnerPermission` | `organization`, with add and change closed |

`TeamRepositoryPermission.alignment_paths` requires those two
organizations to be the same one. That matches the existing Trusts
condition. It does not change how the condition is evaluated.

## Hostile matrix

Real admin requests cover:

- superuser access to managed, foreign, and unowned organizations
- no owner-permission row, then adding one, which turns access on
- deleting that row, retargeting its owner, or changing its operation,
  which turns access off
- team membership or a repository permission alone, which never opens
  the admin
- a missing management `Operation`, which authorizes nothing
- `Organization.objects.authorized(owner, manage)` agreeing with the
  owner changelist, with the grant table in the same SQL as `LIMIT`
- guessed cross-organization change, history, and delete URLs
- forged add and change POSTs, including `_saveasnew` and `_to_field`
- forged add, change, delete, and bulk delete of the authority grant
- foreign-key choices on add and change
- team-member choices, a forged member id, and a real user from
  another organization (allowed as a grant target, not as an account
  edit)
- direct and team grant create, update, and revoke
- `delete_selected`, including a foreign primary key and `select_across`
- direct delete and in-organization cascade
- a corrupt cross-organization grant, which blocks owner cascade
  delete until a superuser removes it
- staff with model permissions and no managed organization
- a non-staff user and an ordinary team member
- superuser preservation, including user and operation admin

A filtered changelist is not the only check.

## Authorization preserved

Admin writes do not bypass the registry. Direct and team paths still
OR. Membership alone and an over-ceiling team grant still deny.
Organization alignment still denies. The organization owner, with no
membership and no direct grant, still has no repository permission.
`AUTH_USER_MODEL` is unchanged. Zero stays absent. Existing fixed-query
tests still apply; an admin-created allow is still one query.

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
