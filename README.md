# django-trusts-gh-permissions

[![Coverage](https://coveralls.io/repos/github/django-trusts/django-trusts-gh-permissions/badge.svg?branch=dev)](https://coveralls.io/github/django-trusts/django-trusts-gh-permissions?branch=dev)

`django-trusts-gh-permissions` is a **bounded reference implementation**
that shows how permissions can emerge from persisted user,
organization, team, and repository relationships on
[django-trusts](https://github.com/django-trusts/django-trusts) 1.x.

It is **not affiliated with GitHub** and is **not** a complete GitHub
authorization clone. Treat it as a worked example, not a migration target.

This bounded example does not reproduce GitHub directly. Its `Team` is
role-like: it groups members, carries an allowed-operation ceiling, and
receives repository grants. It does not model the broader collaboration
behavior of a real GitHub team.

Do **not** list `'trusts'` in `INSTALLED_APPS`. Core is a Python
library, not a Django app.

## Persisted graph

Authorization is compiled from stored rows. A complete team path needs:

- user membership in a team
- team ownership by an organization
- repository ownership by an organization
- a team/repository permission plus a team operation ceiling

A direct path is one `RepositoryCollaborator` row: the user, the
repository, and the selected `permissions`. Organization membership is
not required, so an outside collaborator is just that row.

Organization alignment and the team's allowed operations constrain a
complete team path. Direct and team paths OR together. Deleting any
required persisted relationship removes that path. Malformed,
incomplete, or revoked paths fail closed.

`Alias` is the shared current-name ledger (`name` unique). It is not an
authorization edge and it does not store redirects or older names.
Creating a user reserves an alias, creates the user, creates a personal
organization, and creates an ownership row. Creating a conventional
organization reserves an alias, then creates the organization. Renaming
either name updates the alias and the named object in one transaction.
Deleting the named object releases its current alias.

`User.username` and `Organization.name` stay the real names. The schema
cannot keep them identical to `Alias`, so the supported writes are
`gh_permissions.services`. The example admin registers
`ServiceBackedUserAdmin` for user create, rename, and delete. A raw
queryset write still bypasses that ledger.

A conventional organization has `name` set and `personal_user` null. A
personal organization has `name` null and `personal_user` set, one-to-one
with the user. Its displayed name follows `personal_user.username`.
Exactly one of those shapes is allowed. Every organization points at the
same seeded owner `Group`. There is no `Plan`.

`OrganizationOwnership` is the owner grant: one row per user and
organization, exposed as `Organization.owners`. Every row is an owner.
An organization can have several owners. Someone with no ownership
row does not receive owner permissions. Ordinary organization
membership is not modeled here. `Team` stays separate and does not
grant this bundle.

`Repository.name` is unique per organization and may repeat across
organizations.

## Changing authorization relationships

Supported writes for authorization-bearing rows are
`gh_permissions.services`. Each function loads the persisted
organization by primary key, locks that row, and then requires
`manage_organization` on it. The actor's authority is an
`OrganizationOwnership` row that already exists. A row being created,
a permission being submitted, or an in-memory organization is not
that authority.

An active superuser skips only that inquiry. A persisted inactive
actor is denied before that bypass and before the ownership inquiry,
including an inactive owner and an inactive superuser who already
owns the organization. The same calls still require every parent row
to exist, and a team repository grant still has to stay inside one
organization. A superuser with no ownership row still does not appear
in `.authorized`.

The functions are:

- `add_organization_owner` adds one owner. That is the supported
  `OrganizationOwnership` create and `Organization.owners` add.
- `replace_team_members_and_ceiling` replaces `Team.members` and
  `Team.allowed_operations` together.
- `create_repository_collaborator`, `update_repository_collaborator`,
  `delete_repository_collaborator`, and
  `replace_collaborator_permissions` maintain a direct collaborator
  and that collaborator's permission bundle.
- `create_team_repository_permission`,
  `update_team_repository_permission`, and
  `delete_team_repository_permission` maintain a team grant. The
  stored team organization and the stored repository organization
  must be the same row, including when a team or repository is
  replaced.
- `delete_team` deletes one team.
- `create_repository` creates one repository in a persisted
  organization. The locked organization is the authorization
  boundary.
- `delete_repository` deletes one repository. Collaborator rows and
  team grants cascade after the stored organization is authorized.
- `update_organization_ownership` changes the stored ownership row's
  user, organization, or both. The stored organization is authorized
  before a replacement organization is loaded, and that replacement
  is authorized too.
- `delete_organization_ownership` deletes one stored ownership row.
- `delete_user` releases the username and deletes the personal
  organization with the user. Before that, it locks every conventional
  organization that user owns, in primary-key order. If any of those
  would be left with no owner, the call lists each one and deletes
  nothing.
- `move_team_organization` and `move_repository_organization` refuse
  the move. `Team.organization` and `Repository.organization` stay on
  the stored row.

Changing the user on a collaborator, or the members of a team, does
not cross an organization boundary: users are global grant targets.
Moving a collaborator to another repository authorizes the stored
repository's organization and the replacement repository's
organization. A team grant cannot point at a repository in a
different organization.

An ownership update or delete must leave every surviving conventional
organization with at least one owner. Replacing the user on that same
organization keeps the row, so the sole owner may be handed to someone
else. Moving or deleting the last row is refused. An active superuser
may add the first owner of an organization that has none. The same
superuser may not remove the last owner. A persisted inactive actor is
denied before that bypass and before the ownership inquiry.

`delete_user` omits the user's personal organization from that check
because the personal organization is deleted in the same transaction
and does not survive. A conventional organization this user solely
owns does survive, and blocks the whole deletion.

`Team.organization` and `Repository.organization` are immutable on
this path. A move would re-scope collaborator bundles and team grants,
and a team grant has to stay inside one organization. Authorizing both
the stored organization and the replacement would not decide whether
those grants follow the row, are deleted, or block the move, so the
supported calls resolve the persisted rows and then refuse.

`manage_organization` is the management operation.
`read_repository`, `write_repository`, and `admin_repository` are
repository grants those writes can edit. Team membership and a
collaborator bundle do not grant `manage_organization`.

A raw queryset write does not apply these checks. `register(condition=)`
reads existing trust rows and does not keep an owner on save or delete.
Organization-owner admin calls these functions for every
authorization-bearing edit it still exposes. `OrganizationOwnership`
stays read-only for anyone who is not a superuser; a superuser add,
change, or delete calls the ownership service. `Team.organization`
and `Repository.organization` cannot be changed from an admin form.
`LastOrganizationOwner` from user deletion or an ownership delete is
an admin message, and that write is rolled back.

Requester, team-member, and collaborator relations use
`settings.AUTH_USER_MODEL`.

## Configure

These imports are the object-authorization install. Core `'trusts'`
is absent. `GhAuthorizationBackend` answers object checks. With no
object its permission set is empty, so it does not supply the Django
model permissions stock admin needs. The runnable example adds
`ModelBackend` for that entrance; see Organization-owner admin.

```python
INSTALLED_APPS = (
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'example.apps.ExampleConfig',
    'gh_permissions.apps.GhPermissionsConfig',
)
AUTH_USER_MODEL = 'example.User'

AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
)
```

`example.User` is the test project's user. Its manager mixes in
`PermittedUsersManagerMixin`, which is what makes
`User.objects.permitted(content, perm)` available. The library models
use `settings.AUTH_USER_MODEL` and do not import `example`. A project
that keeps stock `auth.User` still uses `repository.get_permitted_users`.

`GhPermissionsConfig` owns the implementation. The accepted team
registration (contributed at startup) is:

```python
from gh_permissions.models import TeamRepositoryPermission

backend.register(
    trust=TeamRepositoryPermission,
    user="team__members",
    permission="operation",
    content="repository",
    condition=lambda t: (
        t.team.allowed_operations.contains(t.operation)
        & (t.team.organization == t.repository.organization)
    ),
)
```

Direct repository access is the `RepositoryCollaborator` relation
registered beside that team path. `permission` is the terminal
many-to-many to `auth.Permission`:

```python
from gh_permissions.models import RepositoryCollaborator

backend.register(
    trust=RepositoryCollaborator,
    user="user",
    permission="permissions",
    content="repository",
)
```

A `permission=` registration does not contribute to
`get_group_permissions()`.

## Authorize

Object decisions and authorized listings share the same compiled
policy. The permission row is `auth.Permission`. Codename shape is
`gh_permissions.read_repository` and `gh_permissions.write_repository`
(and `gh_permissions.admin_repository`). `Repository.objects.authorized`
takes that permission instance. `has_perm` and `permitted` also accept
the Django permission string as the alias.

```python
from example.models import User
from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import Repository
from trusts.apps import implementation_for_path

registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
    CANONICAL_BACKEND,
).registry
registry.has_permission(user, repository, operation)
Repository.objects.authorized(user, operation)
user.has_perm('gh_permissions.read_repository', repository)
User.objects.permitted(repository, 'gh_permissions.read_repository')
repository.get_permitted_users('gh_permissions.read_repository')
```

`Repository.objects` is the stock core `AuthorizedManager`.
`Repository` mixes in `PermittedUsersMixin`. With the example user
manager, the same rows are `User.objects.permitted(repository, perm)`.

## Install

This is a development reference implementation, not a declared stable
1.0, and not a published PyPI release. `django-trusts` 1.x is also
unpublished, so a clean environment cannot resolve it from PyPI.
Install a local core checkout or artifact first, then this package:

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install ../django-trusts
python -m pip install .
```

Requires **Python 3.12–3.14** and **Django 6.1**.

## Organization-owner admin

Organization administration was `OrganizationOwnerPermission`. That
model is gone. Ownership is an `OrganizationOwnership` row. Startup
registers that row twice, with no condition: once for the organization
and once for that organization's repositories. A non-owner has no
ownership row. `Team` does not grant this bundle.

The shared owner group is seeded with `manage_organization`,
`read_repository`, `write_repository`, and `admin_repository`. Each
permission grants only on an object whose content type matches
`Permission.content_type`. `manage_organization` is the organization
grant. The repository codenames are the grants on that organization's
repositories. `read_repository` on an organization, and
`manage_organization` on a repository, are denials.
`Organization.objects.authorized(user, manage_organization)` is the
organizations that user owns. The org-owner admin adapter uses that
queryset. Conventional organization create, rename, and delete in
admin call the domain services. User create, rename, and delete call
them through `ServiceBackedUserAdmin`. `delete_user` refuses the
deletion when a surviving conventional organization would have no
owner, and the admin shows that refusal instead of deleting the user.
Team, repository, collaborator, team-grant, and superuser ownership
edits call the relationship services. `OrganizationOwnership` rows
stay read-only for non-superusers. `Team.organization` and
`Repository.organization` are immutable in the admin forms.

The runnable example (`tests.settings`, which `manage.py` loads) lists
both backends:

```python
AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
    'django.contrib.auth.backends.ModelBackend',
)
```

`ModelBackend` checks the password and supplies ordinary no-object
Django model permissions, which is what stock admin requires before it
will show a changelist. `GhAuthorizationBackend` and the scoped admin
still decide which organizations and rows that user may see.
`ModelBackend` returns nothing when an object is passed, so a model
permission is not an organization grant. The seeded `example-owner`
proof logs in through this configuration and does not replace
`AUTHENTICATION_BACKENDS`.

Projects that enable this admin install `django.contrib.admin` and list
`ModelBackend` beside `GhAuthorizationBackend`. The boundary is written
up in [docs/org-scoped-admin.md](docs/org-scoped-admin.md).

## Example data

From this repository checkout, migrate and seed one organization. The
command lives in the example project, not in the `gh_permissions` package.

```console
python manage.py migrate
python manage.py seed_example
```

`--organization-name` is optional. The default is `Example Organization`.
The value is checked against `Organization.name` before any rows are
written. The same name with an exact matching graph is safe to run
again: the command reuses those rows and does not create duplicates.
A missing, partial, or different graph for that name fails before it
changes anything. Rows outside that graph are left alone.

The accounts below are development-only. Do not reuse these passwords
in production.

| Username | Development-only password | What it proves |
| --- | --- | --- |
| `example-superuser` | `example-superuser-dev-only` | Application superuser. `authorized` lists persisted grants only. `has_perm` and the permitted-user inquiries include the active superuser. |
| `example-owner` | `example-owner-dev-only` | Staff owner. `OrganizationOwnership` grants `manage_organization` on that organization and `read_repository`, `write_repository`, and `admin_repository` on its repositories. A repository permission on the organization, or `manage_organization` on a repository, is denied. Django model permissions cover those rows, so this account can log in to stock admin and sees only its personal organization and the conventional organizations it owns. |
| `example-direct` | `example-direct-dev-only` | One `RepositoryCollaborator` on `shared-repo` with `read_repository` and `write_repository`. |
| `example-team` | `example-team-dev-only` | Member of team `readers`. `read_repository` on `shared-repo` through membership, the team ceiling, and the team grant. |
| `example-outsider` | `example-outsider-dev-only` | No repository access on `shared-repo`. |

Users and the conventional organization are created through
`gh_permissions.services`. Each user reserves an alias and a personal
organization with its own ownership row. Ordinary organization
membership is not modeled.

Team `readers` allows `read_repository` only. A team grant for
`write_repository` on `shared-repo` is stored and excluded by that
ceiling. Repository `shared-repo` carries both the direct collaborator
and the team path, so those paths can be compared on one repository.

Repeat the default seed with an explicit name:

```console
python manage.py seed_example --organization-name "Example Organization"
```

## Limitations

There is no implicit permission-level hierarchy. Owners receive the
seeded group permissions on their organization and its repositories.
Team membership does not grant that bundle. There is no public anonymous read, nested teams,
invitations, token/app scopes, branch protection, deploy keys, Actions
secrets, forks, CODEOWNERS, visibility matrix, or org default
repository permission. Team membership, the team ceiling, collaborator
bundles, and team grants change through the domain services above. A
raw queryset write does not.

## Documentation

- [django-trusts](https://github.com/django-trusts/django-trusts) — core library
- [Issues](https://github.com/django-trusts/django-trusts-gh-permissions/issues)

Contributor and build history lives in [DEV.md](DEV.md).

Copyright BeeDesk, Inc., 2026 (BSD-2-Clause).
