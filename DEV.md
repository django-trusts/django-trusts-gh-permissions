# DEV.md — internal / transitional record

This file is an internal development record. It may describe
unsupported or superseded states. The user-facing package introduction
is [README.md](README.md). `pyproject.toml` long-description metadata
points at `README.md`, not this file.

## Current pairing

This tree is `django-trusts-gh-permissions==0.1.0.dev0` against the
final core library/documentation line `django-trusts==1.0.0.dev3`
([django-trusts#116](https://github.com/django-trusts/django-trusts/pull/116)
merge `1e19b5d464c067186aada58943c3ee67c44b2aa0`). Pair CI pins that
exact merge. The implementation owner and mixin path did not change
from Step IIb.

Final core ships no Django `AppConfig`, no `kernel_config()`, and no
`trusts.backends.TrustModelBackend`. The mixin stays at
`trusts.backends.TrustModelBackendMixin`.

Never `django-trusts-zero`.

## IIb settings (still the supported install)

`GhPermissionsConfig` is the sole implementation owner. Core is a
library: do not list `'trusts'` in `INSTALLED_APPS`.

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install "django-trusts>=1.0.0.dev3,<2"
python -m pip install -e .
```

```python
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'gh_permissions.apps.GhPermissionsConfig',
]
AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
)
```

`GhAuthorizationBackend` is a mixin-only registry host
(`TrustModelBackendMixin` + `BaseBackend`). It is **not**
`TrustModelBackend`. GH does not use Django auth Permission strings.

`GhPermissionsConfig.ready()` registers the direct-account relation and
the accepted team relation on the owner store via
`implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')`.
It does not call `kernel_config()` and does not install a core AppConfig.

Persisted identity is unchanged: app label `gh_permissions`, migration
`gh_permissions.0001_initial`, tables and content types stay GH-owned.

## Bounded policy

- `Account` membership in an `Organization` (`account.organizations`)
- `Account` membership in a `Team` (`account.teams`); teams belong to
  an organization
- `Repository` belongs to an organization
- A `Team` can receive a repository-scoped `PermissionBundle`
- An `Account` may receive a direct `AccountRepoGrant`
- Complete relation roots OR-compose (direct + team). A partial
  membership or attachment grants nothing.
- Team grants are capped by grant-row organization equality and by
  permission-bundle membership (`All` / `permission_in` / `Equal`)
- Revocation and malformed configuration fail closed

Public relations used to authorize: `account.teams`,
`team.permission_bundles`, `repository.organization`. Callers pass
`Account` and `Operation` **instances**.

The accepted team registration is:

```python
from trusts.core import All, Equal, Ref, permission_in

t = Ref(TeamRepoGrant)
registry.register(
    content=t.repository,
    user=t.team.members,
    permission=t.operation,
    condition=All(
        permission_in(t.team.permission_bundles.operations),
        Equal(t.team.organization, t.repository.organization),
    ),
)
```

That spelling is `gh_permissions.policy.register_team`. Direct is
`register_direct`. There is no aggregate `register_gh_policy()`.

## Usage

```python
from gh_permissions.models import Repository

Repository.objects.authorized(account, operation)
```

Object authorization, authorized listings, and permission enumeration
are projections of the same registrations. Supported object decisions
and listings are SQL-filtered with a fixed query count, before
pagination.

## Intentionally unsupported GH behaviors

Permission levels do not imply lower levels unless the bundle lists
them. There is no org-owner implicit admin, public anonymous read,
nested teams, invitation workflow, token/app scopes, branch protection,
deploy keys, Actions secrets, forks, CODEOWNERS, visibility matrix, or
org default repository permission.

## Test

```
python -m pip install "Django>=6.1,<6.2" coverage
python -m pip install -e .
python -m tests.runtests
python -m django check --settings=tests.settings
```

CI is GitHub Actions (`.github/workflows/ci.yml`) on Python 3.12–3.14
with Django 6.1 against exact core merge `1e19b5d`.

## Code-budget inventory

Counted as physical lines in this tree (generated `0001_initial` is listed with models).

| Category | Files | Why consumer-owned |
|---|---|---|
| Domain models | `models.py` + `0001_initial` | Account, Organization, Team, Repository, PermissionBundle, Operation, TeamRepoGrant, AccountRepoGrant |
| Policy registrations | `policy.py` | Direct `Ref` registration; accepted team spelling; no aggregate helper |
| Registry host | `backends.py` | Mixin-only `AUTHENTICATION_BACKENDS` path; not a compiler copy |
| Ready contribution | `apps.py` | `TrustsImplementationConfig` owner; `register_direct` then `register_team` |
| Framework glue copied locally | **0** | Core owns validation, correlated `EXISTS`, `.authorized` control flow, checks |

Public APIs and the IIb settings cutover are recorded in
[migrates.md](migrates.md).

Package version is **0.1.0.dev0**.

## Superseded pairing notes

Earlier IIb docs paired this tree with core Step I
`django-trusts==1.0.0.dev2` (django-trusts#109 merge `39f1f961`). That
checkpoint, `kernel_config()`, and a core `AppConfig` are gone on the
final core line. Do not restore them.

## License

BSD-2-Clause. Copyright holder is exactly BeeDesk, Inc. Notice year
for this repository is 2026.
