# migrates.md — django-trusts-gh-permissions contributor checklist

This file is the mechanical checklist for the GH-owned
`GhPermissionsConfig` settings cutover and the issue #12 consumer-model
cleanup. It is **not** a user migration product and does **not**
describe a path from GitHub.com. It does **not** implement core
tombstones, Zero changes, examples, or Windows.

Implemented revision: GH-owned owner against final core for
[django-trusts-gh-permissions#6](https://github.com/django-trusts/django-trusts-gh-permissions/issues/6),
documentation alignment in
[django-trusts-gh-permissions#9](https://github.com/django-trusts/django-trusts-gh-permissions/issues/9),
and the bounded model cleanup in
[django-trusts-gh-permissions#12](https://github.com/django-trusts/django-trusts-gh-permissions/issues/12).

Merged GH baseline before this cleanup:
[`eab54b8ba2a36434b93041ec9b53872a183bba5d`](https://github.com/django-trusts/django-trusts-gh-permissions/commit/eab54b8ba2a36434b93041ec9b53872a183bba5d).

## Companion

| Item | Value |
| --- | --- |
| GH package | `0.1.0.dev0` (unchanged) |
| Core requirement | `django-trusts>=1.0.0.dev3,<2` |
| Paired final core | [django-trusts#116](https://github.com/django-trusts/django-trusts/pull/116) merge [`1e19b5d464c067186aada58943c3ee67c44b2aa0`](https://github.com/django-trusts/django-trusts/commit/1e19b5d464c067186aada58943c3ee67c44b2aa0) (library cut [django-trusts#112](https://github.com/django-trusts/django-trusts/pull/112) `11058641`, `django-trusts==1.0.0.dev3`) |
| Zero | **absent** (not a dependency) |

Earlier GH snapshots paired with Step I `django-trusts==1.0.0.dev2`
(merge `39f1f961`). That pin is superseded. Final core deleted
`kernel_config()`, the core `AppConfig`, and
`trusts.backends.TrustModelBackend`.

## Public changes

This unpublished reference **resets the GH schema**. Stored GH rows
from `eab54b8` are not upgraded in place. Do **not** map
`Account.name` onto `AUTH_USER_MODEL`. Drop the database and apply the
replacement `0001_initial`.

| Surface | Old (`eab54b8`) | New (supported) |
| --- | --- |
| Core pin | `django-trusts>=1.0.0.dev3,<2` (pair SHA `1e19b5d`) | **unchanged** |
| `INSTALLED_APPS` | `'gh_permissions.apps.GhPermissionsConfig'` only (no `'trusts'`) | **unchanged** |
| Requester model | `Account` (`name`) | `settings.AUTH_USER_MODEL` (`get_user_model()`) |
| List manager | `GhAuthorizedQuerySet` / `GhAuthorizedManager` | stock `trusts.query.AuthorizedManager` |
| Team ceiling | `PermissionBundle(team, name, operations)` | `Team.allowed_operations` |
| Direct grant row | `AccountRepoGrant(account, repository, operation)` | `UserRepositoryPermission(user, repository, operation)` |
| Team grant row | `TeamRepoGrant(team, repository, operation)` | `TeamRepositoryPermission(team, repository, operation)` |
| Team condition | `permission_in(t.team.permission_bundles.operations)` | `permission_in(t.team.allowed_operations)` |
| Direct registration | `user=d.account` | `user=d.user` |
| Organization members | unused `Organization.members` M2M | **removed** (not a grant edge) |
| Models / migrations | `gh_permissions.0001_initial` (Account graph) | **replaced** `gh_permissions.0001_initial` (clean reset) |
| `.authorized(user, operation)` | owner-present GH queryset | core `AuthorizedQuerySet.authorized` via `configured_implementation_handles()` |

### Old / new settings

Settings stay the owner-only install. `#12` does not change them.

```python
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'gh_permissions.apps.GhPermissionsConfig',
]
AUTHENTICATION_BACKENDS = [
    'gh_permissions.backends.GhAuthorizationBackend',
]
```

```python
# Old donations (removed core helper)
from trusts.apps import kernel_config
kernel_config().configured_backend().registry

# New donations
from trusts.apps import implementation_for_path
implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')
```

### Old / new caller spelling

```python
# Old
Repository.objects.authorized(account, operation)
registry.has_permission(account, repository, operation)

# New
Repository.objects.authorized(user, operation)
registry.has_permission(user, repository, operation)
```

### Startup / failure

| Situation | Supported behavior |
| --- | --- |
| Supported settings above | populate succeeds; `GhPermissionsConfig` is the sole `TrustsImplementationConfig` |
| `'trusts'` listed | not the supported install (core ships no Django app) |
| Canonical backend path missing | `ImproperlyConfigured` from `_validate_ownership` (no silent return) |
| Core below `1.0.0.dev3` / missing helper | `ImproperlyConfigured` at import / ready |
| `kernel_config()` under supported final core | **not importable** |
| Existing GH database from `eab54b8` | **drop and recreate**; no data mapping |

## Unchanged identity

- App label `gh_permissions`
- Migration key `('gh_permissions', '0001_initial')` (contents replaced)
- Two separately reviewable atoms: `register_direct` then `register_team`
- Independent-root OR, fail-closed, fixed query counts
- Mixin-only backend; generic `PlanQueryCompiler`; no Zero historical compiler
- Domain `Operation`; repository-scoped team permission row; separate `policy.py`

`makemigrations gh_permissions --check` is quiet after the reset.
Already-applied GH DBs from the previous `0001_initial` do **not**
match the new schema and must be dropped.

## Contributor checklist

Search application code and settings for:

```text
INSTALLED_APPS.*trusts
from trusts.apps import kernel_config
kernel_config(
from trusts.core_backends
from trusts.zero
django-trusts-zero
register_gh_policy
GhAuthorizedQuerySet
GhAuthorizedManager
class Account
AccountRepoGrant
TeamRepoGrant
PermissionBundle
permission_bundles
account.organizations
Organization.members
```

Then:

- [ ] Remove `'trusts'` from `INSTALLED_APPS`. Keep `'gh_permissions.apps.GhPermissionsConfig'` (or `'gh_permissions'` with `default=True`).
- [ ] Keep `'gh_permissions.backends.GhAuthorizationBackend'`. Do not list `'trusts.backends.TrustModelBackend'`.
- [ ] Replace `kernel_config()` donations with `implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')`.
- [ ] Confirm `ready()` no longer returns silently when the owner/path is missing.
- [ ] Pin `django-trusts>=1.0.0.dev3,<2`. Pair CI uses merge `1e19b5d`.
- [ ] Do not add `django-trusts-zero`. Do not import `trusts.zero` or `trusts.core_backends`.
- [ ] Keep `register_direct` and `register_team` as separate functions. Do not add `register_gh_policy()`.
- [ ] Replace `Account` FKs with `settings.AUTH_USER_MODEL`. Tests use `get_user_model()`.
- [ ] Delete `GhAuthorizedQuerySet` / `GhAuthorizedManager`. Keep `Repository.objects = AuthorizedManager()`.
- [ ] Flatten `PermissionBundle` into `Team.allowed_operations`.
- [ ] Rename `AccountRepoGrant` → `UserRepositoryPermission` and `TeamRepoGrant` → `TeamRepositoryPermission`.
- [ ] Do not register or document `Organization.members` as a Trusts grant edge.
- [ ] Do not add user-repository ownership or organization-owner authorization.
- [ ] Drop any database that applied the pre-#12 `0001_initial`. Recreate from the replacement migration. Do not map `Account.name` to the swappable user model.
- [ ] `python -m django migrate --plan` — no GH operations on an already-current reset database.
- [ ] `python -m django makemigrations gh_permissions --check` — quiet.
- [ ] Confirm object / list / enumeration still OR both roots and fail closed with the same query counts.
- [ ] Confirm stock core `AuthorizedManager` reaches this implementation's registered handles.
- [ ] Leave package version at `0.1.0.dev0`.
- [ ] Do not begin Windows follow-ups, Zero #16, examples, or RST.

## Migration-bot checklist

Treat this as a **clean-database reset**, not an in-place rewrite.

1. Inventory any checkout still on `eab54b8` models (`Account`,
   `PermissionBundle`, `AccountRepoGrant`, `TeamRepoGrant`,
   `GhAuthorizedQuerySet`).
2. Do **not** emit `RenameModel` / `AlterField` operations that pretend
   `Account.name` equals `AUTH_USER_MODEL.username` (or any other user
   field). The requester is now the configured Django user model.
3. Replace `gh_permissions/migrations/0001_initial.py` so it creates
   only: `Organization`, `Operation`, `Team`, `Repository`,
   `TeamRepositoryPermission`, `UserRepositoryPermission`, plus the
   `Team.members` and `Team.allowed_operations` M2Ms. Depend on
   `settings.AUTH_USER_MODEL`.
4. Drop `django_content_type` / table rows for the removed models on
   any leftover local database by dropping the database.
5. Re-run `makemigrations gh_permissions --check` and the authorization
   suite against an empty database.
6. Park user-owned repositories and organization-owner/admin semantics;
   they need persisted role/operation paths that this reset does not add.

## Out of scope

- Windows conversion / README
- Examples
- RST sweep
- User-owned repositories and organization-owner authorization
- A user migration API from `Account.name`
