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

Pin `django-trusts` only, at Step I (`1.0.0.dev2`) merge
[`39f1f9611e214193aec4e97526cf9b54ee689967`](https://github.com/django-trusts/django-trusts/commit/39f1f9611e214193aec4e97526cf9b54ee689967)
(django-trusts#109). Metadata is exactly `django-trusts>=1.0.0.dev2,<2`.
Never `django-trusts-zero`. Package version is `0.1.0.dev0`.

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install "django-trusts @ git+https://github.com/django-trusts/django-trusts.git@39f1f9611e214193aec4e97526cf9b54ee689967"
python -m pip install -e .
```

```python
INSTALLED_APPS = [
    'gh_permissions',
]
AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
)
```

Do **not** list `'trusts'`. Core is a Python dependency only.

`GhAuthorizationBackend` is a mixin-only registry host
(`TrustModelBackendMixin` + `BaseBackend`). It is **not**
`TrustModelBackend`. GH does not use Django auth Permission strings.

`GhPermissionsConfig` subclasses `TrustsImplementationConfig` and owns
`gh_permissions.backends.GhAuthorizationBackend`. `ready()` registers
the direct-account relation and the accepted team relation on this
owner's store through `implementation_for_path`. Missing or duplicate
owner/path configuration fails loud. There is no installed core
AppConfig and no `trusts` schema or migration.

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
from gh_permissions.apps import CANONICAL_BACKEND_PATH
from gh_permissions.models import Repository
from trusts.apps import implementation_for_path

registry = implementation_for_path(
    CANONICAL_BACKEND_PATH,
).configured_backend(CANONICAL_BACKEND_PATH).registry

registry.filter_authorized(Repository.objects.all(), account, operation)
registry.has_permission(account, repository, operation)
registry.permissions_for(account, repository)
```

`Repository.objects` remains `AuthorizedManager()`. Object
authorization, authorized listings, and permission enumeration are
projections of the same registrations. Supported object decisions
and listings are SQL-filtered with a fixed query count, before
pagination.

Step I `AuthorizedQuerySet.authorized` still reads the kernel
accessor. IIb hosts evaluate through the owner registry above until
core Step III retargets `.authorized()` at `configured_handles()`.

## Intentionally unsupported GH behaviors

Permission levels do not imply lower levels unless the bundle lists
them. There is no org-owner implicit admin, public anonymous read,
nested teams, invitation workflow, token/app scopes, branch protection,
deploy keys, Actions secrets, forks, CODEOWNERS, visibility matrix, or
org default repository permission.

## Test

```
python -m pip install "Django>=6.1,<6.2" coverage
python -m pip install "django-trusts @ git+https://github.com/django-trusts/django-trusts.git@39f1f9611e214193aec4e97526cf9b54ee689967"
python -m pip install -e .
python -m tests.runtests
python -m django check --settings=tests.settings
```

CI is GitHub Actions (`.github/workflows/ci.yml`) on Python 3.12–3.14
with Django 6.1 against exact core Step I
`39f1f9611e214193aec4e97526cf9b54ee689967`: tests, fresh migrate,
`manage.py check`, `makemigrations --check`, missing-path startup,
and an sdist/wheel import from outside the checkout. Job names:
`tests vs Step I (Python 3.12)`, `tests vs Step I (Python 3.13)`,
`tests vs Step I (Python 3.14)`, `pair with merged core Step I`,
`package`.

## Code-budget inventory

Counted as physical lines in this tree (generated `0001_initial` is listed with models).

| Category | Lines/files | Why consumer-owned |
|---|---:|---|
| Domain models | 137 + 98 generated migration / 2 files | Account, Organization, Team, Repository, PermissionBundle, Operation, TeamRepoGrant, AccountRepoGrant |
| Policy registrations | 37 / 1 file (`policy.py`) | Direct `Ref` registration; accepted team spelling (`All` / `permission_in` / `Equal`); no aggregate helper |
| Registry host | 19 / `backends.py` | Mixin-only `AUTHENTICATION_BACKENDS` path; not a compiler copy |
| Ready contribution | 88 / `apps.py` | `TrustsImplementationConfig` owner; `register_direct` then `register_team` via `implementation_for_path` |
| Framework glue copied locally | **0** | Core owns validation, correlated `EXISTS`, `.authorized`, checks |
| Tests/fixtures/docs | remaining / this tree | Direct+team acceptance, fail-closed, IIb owner lifecycle, `migrates.md` |

Public APIs added or changed by this revision are recorded in
[migrates.md](migrates.md).

Package version is **0.1.0.dev0**.
