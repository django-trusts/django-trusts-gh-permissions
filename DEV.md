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
`OrganizationMembership`, and `RepositoryCollaborator`. It removes
`UserRepositoryPermission` and `OrganizationOwnerPermission`. Team,
`Team.allowed_operations`, and `TeamRepositoryPermission` are unchanged.
The owner registration is written and not installed: public `condition=`
cannot compare `is_owner` to `True`, and a named filter cannot walk the
membership from the organization or the user. Startup therefore does not
register an unfiltered membership path. The shared owner `Group` is still
seeded with `manage_organization`, `read_repository`, `write_repository`,
and `admin_repository`. Raw user creates and renames, including stock
user admin, do not touch `Alias`.

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
- `OrganizationMembership` is stored. It is not a startup Trusts edge:
  `is_owner == True` is outside the public condition grammar

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
| Domain models | `models.py` + migrations | Alias, Organization, OrganizationMembership, Team, Repository, RepositoryCollaborator, TeamRepositoryPermission. Permission rows are `auth.Permission`, including `manage_organization`. |
| Domain writes | `services.py` | Alias reserve/rename/release, personal organization, shared owner group. Not admin. |
| Policy registrations | `policy.py` | Collaborator and team `handle.register` calls. Owner helper is present and not installed. No aggregate helper |
| Registry host | `backends.py` | Mixin-only `AUTHENTICATION_BACKENDS` path; object-level `auth.Permission` codenames come from the mixin |
| Ready contribution | `apps.py` | `register_collaborator`, then `register_team`, then owner-group seed on `post_migrate` |
| Admin scope plumbing | `_admin_scope.py` | Private stock-admin mixin. No GH model nouns and no registry or compiler calls |
| GH admin adapter | `admin.py` | `manage_organization` lookup, `Organization.objects.authorized`, path declarations, alignment |
| Framework glue copied locally | **0** | Core owns validation, correlated `EXISTS`, `.authorized` control flow, checks |

Package version is **0.1.0.dev0**.

## License

BSD-2-Clause. Copyright holder is exactly BeeDesk, Inc. Notice year
is 2026 for this new repository.
