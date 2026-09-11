# DEV.md — contributor / development history

This file is an internal development record. It may describe
unsupported or superseded states. The user-facing package introduction
is [README.md](README.md). `pyproject.toml` long-description metadata
points at `README.md`, not this file.

## Current pairing

This tree is `django-trusts-gh-permissions==0.1.0.dev0` against the
final core library cut `django-trusts==1.0.0.dev3`
([django-trusts#112](https://github.com/django-trusts/django-trusts/pull/112)
merge `11058641b533e0f8489598e0b1f5cbe5d42a81db`). User-facing core
documentation is django-trusts
[#116](https://github.com/django-trusts/django-trusts/pull/116) merge
`1e19b5d464c067186aada58943c3ee67c44b2aa0`. Pair CI and
`scripts/django-trusts.pin` use that documented final-core revision.

Earlier GH snapshots paired with Step I `django-trusts==1.0.0.dev2`
(merge `39f1f9611e214193aec4e97526cf9b54ee689967`). That pin is
superseded. Never `django-trusts-zero`.

`GhPermissionsConfig` is the sole implementation owner. Core is a
Python dependency only: do **not** list `'trusts'` in `INSTALLED_APPS`.
Final core ships no Django `AppConfig`, no `kernel_config()`, and no
`trusts.backends.TrustModelBackend`. The mixin stays at
`trusts.backends.TrustModelBackendMixin`.

`Requires-Dist`: `django-trusts>=1.0.0.dev3,<2`.

Persisted identity is unchanged: app label `gh_permissions`, migration
`gh_permissions.0001_initial`, tables and content types stay GH-owned.

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

The accepted team registration is `gh_permissions.policy.register_team`.
Direct is `register_direct`. There is no aggregate `register_gh_policy()`.

`GhAuthorizationBackend` is a mixin-only registry host
(`TrustModelBackendMixin` + `BaseBackend`). It is **not**
`TrustModelBackend`. GH does not use Django auth Permission strings.

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
with Django 6.1 against exact final-core merge `1e19b5d464c067186aada58943c3ee67c44b2aa0`.
The suite, migrate/`check`/`makemigrations --check`, wheel RECORD, and
package-metadata scripts must run against that revision without
importing `kernel_config()`, a core `AppConfig`, or
`trusts.backends.TrustModelBackend`.

Public APIs and the IIb settings cutover are recorded in
[migrates.md](migrates.md). That file is a contributor checklist, not
a user migration product.

## Code-budget inventory

Counted as physical lines in this tree (generated `0001_initial` is listed with models).

| Category | Files | Why consumer-owned |
|---|---|---|
| Domain models | `models.py` + `0001_initial` | Account, Organization, Team, Repository, PermissionBundle, Operation, TeamRepoGrant, AccountRepoGrant |
| Policy registrations | `policy.py` | Direct `Ref` registration; accepted team spelling; no aggregate helper |
| Registry host | `backends.py` | Mixin-only `AUTHENTICATION_BACKENDS` path; not a compiler copy |
| Ready contribution | `apps.py` | `TrustsImplementationConfig` owner; `register_direct` then `register_team` |
| Framework glue copied locally | **0** | Core owns validation, correlated `EXISTS`, `.authorized` control flow, checks |

Package version is **0.1.0.dev0**.

## License

BSD-2-Clause. Copyright holder is exactly BeeDesk, Inc. Notice year
is 2026 for this new repository.
