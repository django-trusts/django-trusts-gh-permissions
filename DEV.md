# DEV.md — contributor / development history

This file is an internal development record. It may describe
unsupported or superseded states. The user-facing package introduction
is [README.md](README.md). `pyproject.toml` long-description metadata
points at `README.md`, not this file.

## Current pairing

This tree is `django-trusts-gh-permissions==0.1.0.dev0` against the
final core library floor `django-trusts>=1.0.0.dev3,<2`
([django-trusts#112](https://github.com/django-trusts/django-trusts/pull/112)
merge `11058641b533e0f8489598e0b1f5cbe5d42a81db`). Pair CI,
`requirements.txt`, and `scripts/django-trusts.pin` point at
django-trusts `dev` commit
`7503ae83267771abdce920f26ad762a4ee491f36`, the merge of
[#264](https://github.com/django-trusts/django-trusts/pull/264).
Do not float past that commit. That merge is what lets `permission=`
end on one forward to-many step to `auth.Permission`.

[#27](https://github.com/django-trusts/django-trusts-gh-permissions/issues/27)
adds `Alias`, personal versus conventional organizations,
`OrganizationOwnership`, and `RepositoryCollaborator`. It removes
`UserRepositoryPermission` and `OrganizationOwnerPermission`. Team,
`Team.allowed_operations`, and `TeamRepositoryPermission` are unchanged.
`OrganizationOwnership` is owners only. The row is the grant, so
startup registers it with no condition: `owner_group` permissions
apply to the organization and to that organization's repositories.
A user with no ownership row is not an owner. Ordinary organization
membership is deferred. Team does not carry
that bundle. The shared owner `Group` is seeded with
`manage_organization`, `read_repository`, `write_repository`, and
`admin_repository`. The example admin uses `ServiceBackedUserAdmin`.
A raw queryset user create or rename does not touch `Alias`.

[#28](https://github.com/django-trusts/django-trusts-gh-permissions/issues/28)
stage 8 adds domain services for the settled authorization-bearing
writes: add an owner; replace team members and the team ceiling
together; collaborator create, update, delete, and permission-bundle
replacement; team-repository grant create, update, and delete; and
team delete. Each call is one `transaction.atomic`. It
`select_for_update`s the persisted organization before
`Organization.objects.authorized(..., manage_organization)` and before
the mutation. Submitted instances are not evidence: the functions
resolve primary keys inside that transaction. An update authorizes the
stored organization and any replacement organization. A team-repository
grant requires the locked team organization and the locked repository
organization to be the same row, on the stored pair and on any
replacement. `QuerySet.create` still does not call `clean`, so the
service performs that comparison itself. Missing, duplicate,
cross-organization, and undefined shapes raise before any write. An
active superuser (`is_active and is_superuser`) skips only the
inquiry. A persisted inactive actor is denied before that bypass and
before the ownership inquiry, including an inactive owner and an
inactive superuser who already owns the organization. Existence and
same-organization checks still apply. `.authorized` does not gain a
row for that superuser.

The next #28 slice adds ownership update and delete, the
`delete_user` preflight, and a refusal for `Team.organization` and
`Repository.organization` moves. A conventional organization that
survives the write must keep an owner. `delete_user` locks every
conventional organization that user owns, in primary-key order,
inside the same transaction as the delete. The id list can be an
ordinary read. The decision is a later `select_for_update` of the
ownership rows, after that organization lock, so a repeatable-read
snapshot from the id list is not the owner count. The same locking
recount guards ownership update and delete.
`LastOrganizationOwner` lists each organization that would be left
empty, and the transaction rolls the deletion back. The user's
personal organization is deleted with the user, so it is not one of
those survivors. An active
superuser may add the first owner of an organization that has none,
and may not remove the last one. A persisted inactive actor is still
denied before that bypass and before the ownership inquiry.

`move_team_organization` and `move_repository_organization` lock the
stored row and both organizations, then refuse. A move would re-scope
collaborator bundles and team grants. Checking `manage_organization`
on both boundaries would not decide whether those grants follow, are
deleted, or block the move. That decision is outside this slice, so
the supported path leaves the stored foreign key in place. Raw
queryset writes still do not run the service checks.
`register(condition=)` was checked at core `7503ae83`: it compiles
into the trust-row `EXISTS`, reads rows that already exist, has no
aggregate that can require another owner, and core installs no save
or delete signal. The owner count stays in the service. No new
codename, no new `handle.register`, and no schema migration. Admin
does not call the new ownership or move functions.
`OrganizationOwnershipAdmin` stays read-only for non-superusers.
`ServiceBackedUserAdmin` already calls `delete_user`, so the
preflight applies to that existing hook.

The permission terminal is `auth.Permission`, with codenames
`read_repository`, `write_repository`, and `admin_repository`.
`Repository` mixes in `PermittedUsersMixin`. The example/test user
(`example.User`) mixes `PermittedUsersManagerMixin` into its manager so
`User.objects.permitted(content, perm)` is exercised. The library does
not require that user. `0001_initial` is unchanged.
`0002_auth_permission_terminal` is an irreversible reset: before either
operation foreign key is retargeted, it deletes every
`UserRepositoryPermission` and `TeamRepositoryPermission` row, and
removing then re-adding `Team.allowed_operations` drops the ceiling
through-table. Old `operation_id` values are not copied onto
`auth.Permission`. Migrating backwards raises `IrreversibleError` and
does not restore those grants. `0003_organization_owner_permission`
adds `Organization.manage_organization` and
`OrganizationOwnerPermission` after that reset. It does not rewrite
`0002_auth_permission_terminal`.

The earlier companion pin `781a33dfc46fa3ba10a5e8b634de2d47780e857b`
(django-trusts#241) is superseded for this pin. The earlier
`handle.register` plus symbolic-condition pin from django-trusts
[#213](https://github.com/django-trusts/django-trusts/pull/213)
integration train `DEV_register_api_train` head
`a909eaeb8e087977fb1c1076682aab0b5ad754c5` (stacks approved #206 docs,
#208 `register(*, trust=...)`, and #211 symbolic `condition=`) is
superseded for this pin.

The earlier documented `handle.register_relationship` pair
[#174](https://github.com/django-trusts/django-trusts/pull/174)
`bc25cd9524b12cd15047a401d12b82f813b6e500` is superseded for this pin.

The earlier documented temporary `handle.register` forwarder pair
[#158](https://github.com/django-trusts/django-trusts/pull/158)
`e9fd4cd4f77624f3d5351b505808c1d6fa8bcbc4` is superseded for this pin.

The earlier documented core README merge
[#116](https://github.com/django-trusts/django-trusts/pull/116)
`1e19b5d464c067186aada58943c3ee67c44b2aa0` is superseded for this pin.

Earlier GH snapshots paired with Step I `django-trusts==1.0.0.dev2`
(merge `39f1f9611e214193aec4e97526cf9b54ee689967`). That pin is
superseded. Never `django-trusts-zero`.

`GhPermissionsConfig` is the sole implementation owner. Core is a
Python dependency only: do **not** list `'trusts'` in `INSTALLED_APPS`.
Final core ships no Django `AppConfig`, no `kernel_config()`, and no
`trusts.backends.TrustModelBackend`. The mixin stays at
`trusts.backends.TrustModelBackendMixin`.

`Requires-Dist`: `django-trusts>=1.0.0.dev3,<2`.

[#12](https://github.com/django-trusts/django-trusts-gh-permissions/issues/12)
reset the unpublished GH schema: `Account` became `AUTH_USER_MODEL`,
`PermissionBundle` flattened to `Team.allowed_operations`, grant rows
were renamed to `UserRepositoryPermission` /
`TeamRepositoryPermission`, unused `Organization.members` was removed,
and listings use stock `AuthorizedManager`. App label stays
`gh_permissions`; `0001_initial` was regenerated for a clean-database
reset.

## What the archived Step IIb README got wrong

The previous top-level README described pairing against Step I
`1.0.0.dev2` / merge `39f1f961`, treated `kernel_config()` as a live
`LookupError` helper, and inventoried CI against that Step I SHA.
Those statements are false after the final core cut. Final core
deleted `kernel_config()`, the core `AppConfig`, and
`trusts.backends.TrustModelBackend`. Supported GH startup still uses
`implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')`
and never installs `'trusts'`.

## Bounded policy (still true)

- Configured user membership in a `Team` (`user.teams`); teams belong
  to an organization
- `Repository` belongs to an organization
- A `Team` has a direct `allowed_operations` ceiling
- A user may receive a direct `RepositoryCollaborator` bundle
- A team may receive a repository-scoped `TeamRepositoryPermission`
- Complete relation roots OR-compose (collaborator + team). A partial
  membership or attachment grants nothing.
- Team permissions are capped by grant-row organization equality and by
  the team operation ceiling (`.contains` / `==` / `&`)
- Revocation and malformed configuration fail closed
- `OrganizationOwnership` is the owner grant and a startup Trusts edge.
  Team membership does not grant that bundle

Public relations used to authorize: `user.teams`,
`team.allowed_operations`, `repository.organization`, and
`collaborator.permissions`. Callers pass `AUTH_USER_MODEL` and
`auth.Permission` **instances**. `Repository.objects.authorized` takes
the permission instance. `has_perm` and `permitted` accept
`gh_permissions.read_repository` (and the write and admin codenames)
as the string alias.

The accepted team registration is `gh_permissions.policy.register_team`.
Direct repository access is `register_collaborator`. Both take the
configured `BackendHandle` and call `handle.register` with `trust=`
plus Django `__` path strings.
Team adds a one-argument symbolic `condition=` using `.contains`, `==`,
and `&`. Literal Python `in` is unsupported. There is no aggregate
`register_gh_policy()`.

`GhAuthorizationBackend` is a mixin-only registry host
(`TrustModelBackendMixin` + `BaseBackend`). It is **not**
`TrustModelBackend`. `Repository.objects.authorized` takes an
`auth.Permission` instance, while `has_perm` and the permitted-user
adapters accept either that instance or its Django permission string
as documented.

Application authors own ordinary relational models plus compact
registrations. Core owns validation, correlated query construction,
authorization control flow, and the fixed-query projections (object,
authorized queryset, permission enumeration). Copied framework
authorization glue in this repository is **zero**.

## Settings (supported)

```python
INSTALLED_APPS = (
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'gh_permissions.apps.GhPermissionsConfig',
)
AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
)
```

`GhAuthorizationBackend` is empty when no object is passed. The
runnable example (`tests.settings`, loaded by `manage.py`) keeps that
backend and adds `ModelBackend`:

```python
AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
    'django.contrib.auth.backends.ModelBackend',
)
```

`ModelBackend` authenticates the password and supplies the no-object
Django model permissions stock admin requires. It returns no
permissions when an object is passed, so organization scope stays on
`GhAuthorizationBackend` and the admin adapter. A model permission is
not an organization grant.

## Verification

```
python -m pip install "Django>=6.1,<6.2" coverage
python -m pip install -e .
python -m tests.runtests
python -m django check --settings=tests.settings
```

CI is GitHub Actions (`.github/workflows/ci.yml`) on Python 3.12–3.14
with Django 6.1 against exact paired-core head `7503ae83267771abdce920f26ad762a4ee491f36`.
The suite, migrate/`check`/`makemigrations --check`, wheel RECORD, and
package-metadata scripts must run against that revision without
importing `kernel_config()`, a core `AppConfig`, or
`trusts.backends.TrustModelBackend`.

Public APIs and the IIb settings cutover plus the #12 schema reset are
recorded in [migrates.md](migrates.md). That file is a contributor
checklist, not a user migration product.

## Code-budget inventory

Counted as physical lines in this tree (generated `0001_initial` is listed with models).

| Category | Files | Why consumer-owned |
|---|---|---|
| Domain models | `models.py` + migrations | Alias, Organization, OrganizationOwnership, Team, Repository, RepositoryCollaborator, TeamRepositoryPermission. Permission rows are `auth.Permission`, including `manage_organization`. |
| Domain writes | `services.py` | Alias reserve/rename/release, personal organization, shared owner group, and the settled authorization-relationship writes. Not admin. |
| Policy registrations | `policy.py` | Collaborator, team, and owner `handle.register` calls. No aggregate helper |
| Registry host | `backends.py` | Mixin-only `AUTHENTICATION_BACKENDS` path; object-level `auth.Permission` codenames come from the mixin |
| Ready contribution | `apps.py` | `register_collaborator`, `register_team`, `register_organization_owner`, then owner-group seed on `post_migrate` |
| Admin scope plumbing | `_admin_scope.py` | Private stock-admin mixin. No GH model nouns and no registry or compiler calls |
| GH admin adapter | `admin.py` | `manage_organization` lookup, `Organization.objects.authorized`, path declarations, alignment |
| Framework glue copied locally | **0** | Core owns validation, correlated `EXISTS`, `.authorized` control flow, checks |

Package version is **0.1.0.dev0**.

## License

BSD-2-Clause. Copyright holder is exactly BeeDesk, Inc. Notice year
is 2026 for this new repository.
