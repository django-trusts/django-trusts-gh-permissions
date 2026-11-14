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

A repository may also contain `Issue` rows connected through an optional
`parent`. An `IssuePermissions` row assigns a user a permission bundle at one
issue. The `triage_issue` example permission reaches that issue and bounded
sub-issues. It does not reach an ancestor or a sibling.

Organization alignment and the team's allowed operations constrain a
complete team path. Direct and team paths OR together. Deleting any
required persisted relationship removes that path. Malformed,
incomplete, or revoked paths fail closed.

The [repository delegation example](docs/repository-delegation.md) adds a
sponsor-selected, organization-approved bridge. It is limited by both its own
repository/permission scope and the sponsor's live ordinary authority. The
sponsor's ordinary authority remains the union of direct, team, and owner
paths; delegation itself is one level only.

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

## Bounded sub-issue triage

The sub-issue example is the concrete consumer of provisional `along=`.
[GitHub documents](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
a full parent/sub-issue hierarchy, permits an existing issue to be re-parented,
and limits nesting to eight levels. Each issue keeps its own repository
identity while the optional `parent` defines the hierarchy; this also permits
the cross-repository sub-issues supported by GitHub. GitHub limits those
cross-repository relationships to repositories with the same owner. This
bounded example does not reproduce that separate write-time owner check.

`models.py` registers the triage assignment as:

```python
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import IssuePermissions

GhAuthorizationBackend().get_registry().register(
    trust=IssuePermissions,
    user="user",
    permission="permissions",
    content="issue",
    along=(
        lambda t: ((t.issue.reset_inheritance == False) | (t.issue.reset_inheritance == None)) & t.issue.parent,
        8,
    ),
)
```

Depth zero is the assigned issue. Eight parent edges are included; a ninth is
denied. That relative query-safety ceiling is not the same as GitHub's
absolute eight-level tree limit. Valid application writes remain responsible
for the absolute level rule, rejecting cycles, and the separate same-owner
rule.

`Issue.reset_inheritance` is nullable. NULL is unset and inherits, and
false inherits. True cuts grants inherited from ancestors at that issue
and below it. A grant on that issue still applies.

Forward authorization answers whether one user may triage one issue.
Reverse inquiry answers who may triage a selected issue:

```python
user.has_perm("gh_permissions.triage_issue", issue)
Issue.objects.authorized(user, triage_permission)
User.objects.permitted(issue, triage_permission)
issue.get_permitted_users(triage_permission)
```

Removing the permission from the assignment, or deleting the assignment,
revokes both directions immediately. The policy lock records the same
bounded recursive SQL for forward and reverse inquiries.

Re-parenting changes effective authority immediately because the assignment
follows persisted issue identity and the current parent edges. This is a
GH-style finer-grained example policy; it does not claim that GitHub's native
repository-level triage role inherits through sub-issues this way.

## Changing authorization relationships

Supported writes for authorization-bearing rows are
`gh_permissions.services`. Each function loads the persisted
organization by primary key, locks that row, and then requires
`manage_organization` on it. The actor's authority is an
`OrganizationOwnership` row that already exists. A row being created,
a permission being submitted, or an in-memory organization is not
that authority.

An active superuser needs that same ownership row. The superuser
flag is not a grant, and this package does not register a superuser
override. A persisted inactive actor is denied before the ownership
inquiry, including an inactive owner and an inactive superuser who
already owns the organization. The same calls still require every
parent row to exist, and a team repository grant still has to stay
inside one organization. A superuser with no ownership row does not
appear in `.authorized` or in the permitted-user inquiries.

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
else. Moving or deleting the last row is refused. An organization
with no owner cannot gain its first owner through these calls,
including when the actor is an active superuser. A persisted inactive
actor is denied before the ownership inquiry.

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
stays read-only for every actor, including a superuser. `Team.organization`
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
registration, called from the end of `models.py`, is:

```python
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import TeamRepositoryPermission

GhAuthorizationBackend().get_registry().register(
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
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import RepositoryCollaborator

GhAuthorizationBackend().get_registry().register(
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
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import Repository

GhAuthorizationBackend().has_perm(
    user, 'gh_permissions.read_repository', repository,
)
Repository.objects.authorized(user, operation)
user.has_perm('gh_permissions.read_repository', repository)
User.objects.permitted(repository, 'gh_permissions.read_repository')
repository.get_permitted_users('gh_permissions.read_repository')
```

`Repository.objects` is `RepositoryQuerySet`. That queryset mixes
`PermittedQuerySetMixin` onto `AuthorizedQuerySet`, so
`Repository.objects.permitted(permission, user)` lists rows for a
permission string and `.authorized(user, permission)` stays the
lower-level projection. `Organization` and the other models keep the
stock `AuthorizedManager`. `Repository` mixes in `PermittedUsersMixin`.
With the example user manager, the same users are
`User.objects.permitted(repository, perm)`.

## Install

This is a development reference implementation, not a declared stable
1.0, and not a published PyPI release. `django-trusts` 1.x is also
unpublished, so a clean environment cannot resolve it from PyPI.
Install a local core checkout or artifact first, then this package:

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install ../django-trusts
python -m pip install .
python -m pip install "djangorestframework==3.18.1"
```

Requires **Python 3.12–3.14** and **Django 6.1**.
`djangorestframework` 3.18.1 is only for the repository API below. It is
not a dependency of the `gh_permissions` package. That release lists
Django 6.1 and Python 3.12–3.14 in its own classifiers and tox matrix.

## Organization-owner admin

Organization administration was `OrganizationOwnerPermission`. That
model is gone. Ownership is an `OrganizationOwnership` row. `models.py`
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
Team, repository, collaborator, and team-grant edits call the
relationship services when the actor has `manage_organization` on
the organization. `OrganizationOwnership` rows stay read-only.
`Team.organization` and `Repository.organization` are immutable in
the admin forms. A superuser sees the same organizations as any
other user with the same ownership rows.

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
A seed created before the sub-issue example receives that wholly absent slice
once. A partial or different base graph or sub-issue slice fails before it
changes anything. Rows outside that graph are left alone.

The accounts below are development-only. Do not reuse these passwords
in production.

| Username | Development-only password | What it proves |
| --- | --- | --- |
| `example-superuser` | `example-superuser-dev-only` | Application superuser. `authorized` and the permitted-user inquiries list persisted grants only. Django's own `User.has_perm` still returns true for an active superuser; this package's backend does not, unless the superuser has a grant. |
| `example-owner` | `example-owner-dev-only` | Staff owner. `OrganizationOwnership` grants `manage_organization` on that organization and `read_repository`, `write_repository`, and `admin_repository` on its repositories. A repository permission on the organization, or `manage_organization` on a repository, is denied. Django model permissions cover those rows, so this account can log in to stock admin and sees only its personal organization and the conventional organizations it owns. |
| `example-direct` | `example-direct-dev-only` | One `RepositoryCollaborator` on `shared-repo` with `read_repository` and `write_repository`, plus sub-issue triage authority at issue `#101`. |
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

The same repository has issue `#100` with children `#101` and `#103`, and
`#101` has child `#102`. `example-direct` receives `triage_issue` at `#101`,
so `#101` and `#102` are permitted while parent `#100` and sibling `#103` are
denied. Reverse inquiry on `#102` returns `example-direct` among ordinary
users.

Repeat the default seed with an explicit name:

```console
python manage.py seed_example --organization-name "Example Organization"
```

## Repository API

The runnable example (`tests.settings`, which `manage.py` loads) adds
`rest_framework` and mounts one viewset:

| Request | Permission | Result |
| --- | --- | --- |
| `GET /api/repositories/` | `gh_permissions.read_repository` | Page of rows from `Repository.objects.permitted` |
| `GET /api/repositories/{pk}/` | `gh_permissions.read_repository` | DRF `get_object()` on that same queryset |
| `POST /api/repositories/` | `gh_permissions.manage_organization` | `gh_permissions.services.create_repository` |

`example.api.RepositoryViewSet.action_permissions` is the only
action-to-permission map. List and retrieve both use
`gh_permissions.read_repository`. The view calls
`Repository.objects.permitted` with that string and the request user
before pagination. `permitted` binds the codename to the repository
content type, so another model in the same app may reuse the codename
without becoming this permission. An inactive principal gets an empty
queryset from `permitted`, which is what
`user.has_perm(permission_string, repository)` does for an inactive
owner, collaborator, or team member. `has_perm` is only called with
that string. Serialization reads `id`, `name`, and `organization_id`
off those rows, so the page does not run a permission query per
repository.

An active superuser is the Django exception on `has_perm`. That call
is true for every string, including a repository the superuser was
not granted. `permitted` still returns only persisted grants, and
this API follows that queryset. A superuser with no grant gets an
empty list and a 404 for that primary key. `create_repository`
requires `manage_organization` on the organization for that superuser
too, so a create with no ownership row is 403 and writes nothing.

Retrieve uses one status for every primary key the caller cannot
read. A malformed key, a missing key, and a real key outside the
permitted queryset are all **404** with `{"detail": "Not found."}`.
The body does not include the repository. If the object check
disagrees with the queryset, that is a 404 as well. An authenticated
caller with no rows gets **200** and an empty page, not an error.
`is_staff` is not repository authority. A model permission stored for
`ModelBackend` is not either: that backend does not answer object
checks, and the list stays empty.

Authentication is a bearer token or a session. This bearer map is a
development deployment harness, not production token infrastructure.
Bearer authentication is listed first. It reads `Authorization: Bearer <token>`
and looks that token up in `EXAMPLE_API_TOKENS`, a mapping of token to
username. `tests.settings` fills the mapping from `EXAMPLE_API_TOKEN_OWNER`
(`example-owner`), `EXAMPLE_API_TOKEN_DIRECT` (`example-direct`), and
`EXAMPLE_API_TOKEN_OUTSIDER` (`example-outsider`) when the process
starts. An unset or empty variable adds no entry. Generate each value
with `secrets.token_urlsafe(32)` (43 characters). A shorter value is a
configuration error and the process does not start. The tokens are not
in the source and are not in the seed. Comparison walks every
configured token and uses `hmac.compare_digest` on SHA-256 digests.
An unknown token fails authentication. An inactive user is rejected
because `is_active` is false, the same reason `has_perm` denies that
user. The
authenticator returns no credential marker, so `request.auth` does not
keep the token. A request with no `Authorization` header still uses
session login.

The harness has no expiry, no rotation protocol, no per-token scope,
no durable revocation or audit record, and no protection against
username reuse.

Anonymous requests, and requests with a missing or unknown bearer
token, are rejected before the queryset. Because bearer
authentication is first, DRF sends `WWW-Authenticate: Bearer` and the
status is **401**. The missing-credential detail is `Authentication
credentials were not provided.` An unknown token's detail is `Invalid
token.` No repository row is queried.

```console
curl -s -H "Authorization: Bearer <token>" http://127.0.0.1:8000/api/repositories/
```

Replace `<token>` with the value of one of those environment
variables. `python manage.py runserver` serves the API after
`seed_example`. This is the header a client, including an MCP agent,
sends with curl. There is no OAuth, scope, or separate agent user.

`example-owner` is staff, so `/admin/login/` can start that session.
`example-direct` and `example-outsider` are not staff, so admin login
cannot. Their live calls use `EXAMPLE_API_TOKEN_DIRECT` and
`EXAMPLE_API_TOKEN_OUTSIDER`: the direct collaborator can list and is
refused on create, and the outsider's retrieve is 404. `example-team`
is not staff either and has no token variable. The test client covers
that member.

Create does not insert a repository itself. It calls
`create_repository`, which locks the submitted organization and
requires `manage_organization`. The serializer accepts `name` and
`organization_id` only. Any other key, including a user, team,
collaborator, permission, or repository, is rejected and writes
nothing. A denied, missing, or cross-organization parent is **403**
with `{"detail": "Not allowed."}` and writes nothing. A direct
collaborator or a team member does not gain this from a repository
grant. Team `readers` stores `write_repository` and the read-only
ceiling excludes it, so that grant is not write authority and it is
not organization management. An active superuser without an ownership
row is denied by the same service. The view does not add a bypass.

Pagination slices the permitted queryset. `page_size` defaults to 25
and can be set up to 100. An unauthorized repository with a lower
primary key is not the first row of the first page.

These query counts are one already-authenticated JSON request on
Django 6.1.3, DRF 3.18.1, and SQLite. They do not grow when more
visible or hidden repositories are added.

| Request | Queries | What they are |
| --- | --- | --- |
| list | 2 | count of the distinct permitted queryset, one page of that queryset |
| retrieve | 2 | permitted lookup by primary key, `has_perm` object check |
| denied retrieve | 1 | permitted lookup misses; no object check |
| create | 8 | transaction savepoint, lock the actor, lock the organization, `manage_organization` row, authorized existence check, duplicate-name check, insert, release savepoint |

From a checkout with the dependencies installed, one allowed list and
one denied retrieve (plus a denied create) are:

```console
python scripts/drf_authorization_proof.py
```

The script migrates a temporary database, runs `seed_example`, and
uses the DRF test client with session login. It prints the owner
list, the outsider retrieve, and the direct collaborator's refused
create. It does not print the development passwords.

## Limitations

There is no implicit permission-level hierarchy. Owners receive the
seeded group permissions on their organization and its repositories.
Team membership does not grant that bundle. There is no public anonymous read, nested teams,
invitations, token/app scopes, branch protection, deploy keys, Actions
secrets, forks, CODEOWNERS, visibility matrix, or org default
repository permission. Team membership, the team ceiling, collaborator
bundles, and team grants change through the domain services above. A
raw queryset write does not. The path-review example is not CODEOWNERS and
does not make repository permissions imply path permissions.

## Documentation

- [django-trusts](https://github.com/django-trusts/django-trusts) — core library
- [Issues](https://github.com/django-trusts/django-trusts-gh-permissions/issues)

Contributor and build history lives in [DEV.md](DEV.md).

Copyright BeeDesk, Inc., 2026 (BSD-2-Clause).
