# django-trusts-gh-permissions

Minimal GH-style organization, team, and repository authorization built
on [`django-trusts`](https://github.com/django-trusts/django-trusts).
This package is an independent relational-policy example. It is **not**
affiliated with or endorsed by any external service and does not claim
complete compatibility.

Application authors own ordinary relational models plus compact
registrations. Core owns validation, correlated query construction,
authorization control flow, and the fixed-query projections (object,
authorized queryset, permission enumeration). Copied framework
authorization glue in this repository is **zero**.

## Dependency

Pin `django-trusts` only, at C2 merge
[`db5a41ed66478b79e10b0a066c8c0bca8fbe7882`](https://github.com/django-trusts/django-trusts/commit/db5a41ed66478b79e10b0a066c8c0bca8fbe7882).
Never `django-trusts-zero`. Package version is `0.1.0.dev0`.

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install -e .
```

```python
INSTALLED_APPS = [
    'trusts',
    'gh_permissions',
]
AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
)
```

`GhAuthorizationBackend` is a mixin-only registry host
(`TrustModelBackendMixin` + `BaseBackend`). It is **not**
`TrustModelBackend`. GH does not use Django auth Permission strings.

`GhPermissionsConfig.ready()` registers the direct-account relation on
the kernel store. The kernel app label is `trusts_core`. There is no
`trusts` schema or migration.

## Bounded policy

- `Account` membership in an `Organization` (`account.organizations`)
- `Account` membership in a `Team` (`account.teams`); teams belong to
  an organization
- `Repository` belongs to an organization
- A `Team` can receive a repository-scoped `PermissionBundle`
- An `Account` may receive a direct `AccountRepoGrant`
- Complete relation roots OR-compose; a partial membership or
  attachment grants nothing
- Team grants are capped by grant-row organization equality and by
  permission-bundle membership (accepted C2 typed predicates; see
  [MISSING_CORE.md](MISSING_CORE.md))
- Revocation and malformed configuration fail closed

Public relations used to authorize: `account.teams`,
`team.permission_bundles`, `repository.organization`. Callers pass
`Account` and `Operation` **instances**.

## Usage

```python
from gh_permissions.models import Repository

Repository.objects.authorized(account, operation)
```

Object authorization, authorized listings, and permission enumeration
are projections of the same registrations. Supported object decisions
and listings are SQL-filtered with a fixed query count, before
pagination.

## G1 missing-core stop

The accepted team registration (membership hop + `All` /
`permission_in` / `Equal`) is **not** expressible on public C2
`db5a41ed`. Direct grants are. This tree does not invent a
consumer-local dialect. Details and the failing minimal test are in
[MISSING_CORE.md](MISSING_CORE.md) and
`tests.test_missing_core`.

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
with Django 6.1: tests, fresh migrate, `manage.py check`, and an
sdist/wheel import from outside the checkout. Job names:
`tests (Python 3.12)`, `tests (Python 3.13)`, `tests (Python 3.14)`,
`package`.

## Code-budget inventory

Counted as physical lines in this tree (generated `0001_initial` is listed with models).

| Category | Lines/files | Why consumer-owned |
|---|---:|---|
| Domain models | 137 + 98 generated migration / 2 files | Account, Organization, Team, Repository, PermissionBundle, Operation, TeamRepoGrant, AccountRepoGrant |
| Policy registrations | 60 / 1 file (`policy.py`) | Direct `Ref` registration; accepted team spelling (`All` / `permission_in` / `Equal`) |
| Registry host | 14 / `backends.py` | Mixin-only `AUTHENTICATION_BACKENDS` path; not a compiler copy |
| Ready contribution | 27 / `apps.py` | `configured_backend().registry.register(...)` |
| Framework glue copied locally | **0** | Core owns validation, correlated `EXISTS`, `.authorized`, checks |
| Tests/fixtures/docs | remaining / this tree | Direct acceptance, fail-closed, kernel topology, missing-core stop |

This reconstitution does not change a prior shipped public API (the
active baseline was README-only). There is no `migrates.md` in this PR.

Package version is **0.1.0.dev0**.
