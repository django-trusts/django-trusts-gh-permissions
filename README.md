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

Pin `django-trusts` only, at C2 + #98 merge
[`595e2f9f0cc97f8744c1178f3e384dba5787773c`](https://github.com/django-trusts/django-trusts/commit/595e2f9f0cc97f8744c1178f3e384dba5787773c)
(reviewed head
[`5df5eab643d1da39c2ff86bc838b409377590da5`](https://github.com/django-trusts/django-trusts/commit/5df5eab643d1da39c2ff86bc838b409377590da5)).
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

`GhPermissionsConfig.ready()` registers the direct-account relation and
the accepted team relation on the kernel store. The kernel app label is
`trusts_core`. There is no `trusts` schema or migration.

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
  permission-bundle membership (`All` / `permission_in` / `Equal` on
  public C2)
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
with Django 6.1: tests, fresh migrate, `manage.py check`, and an
sdist/wheel import from outside the checkout. Job names:
`tests (Python 3.12)`, `tests (Python 3.13)`, `tests (Python 3.14)`,
`package`.

## Code-budget inventory

Counted as physical lines in this tree (generated `0001_initial` is listed with models).

| Category | Lines/files | Why consumer-owned |
|---|---:|---|
| Domain models | 137 + 98 generated migration / 2 files | Account, Organization, Team, Repository, PermissionBundle, Operation, TeamRepoGrant, AccountRepoGrant |
| Policy registrations | 35 / 1 file (`policy.py`) | Direct `Ref` registration; accepted team spelling (`All` / `permission_in` / `Equal`); no aggregate helper |
| Registry host | 14 / `backends.py` | Mixin-only `AUTHENTICATION_BACKENDS` path; not a compiler copy |
| Ready contribution | 28 / `apps.py` | `register_direct` then `register_team` |
| Framework glue copied locally | **0** | Core owns validation, correlated `EXISTS`, `.authorized`, checks |
| Tests/fixtures/docs | remaining / this tree | Direct+team acceptance, fail-closed, kernel topology, `migrates.md` |

Public APIs added by this reconstitution are recorded in
[migrates.md](migrates.md) (old behavior is README-only / unavailable).

Package version is **0.1.0.dev0**.
