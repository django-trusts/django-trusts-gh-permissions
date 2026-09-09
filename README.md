# django-trusts-gh-permissions

Minimal GH-style organization, team, and repository authorization built
on [`django-trusts`](https://github.com/django-trusts/django-trusts).
This package is an independent relational-policy example. It is **not**
affiliated with or endorsed by any external service and does not claim
complete compatibility.

Application authors own the domain models and the explicit policy
registrations. The framework owns authorization execution, queryset
filtering, validation, `ObjectAuthorizationBackend`,
`require_authorized`, `AuthorizedModelAdmin`, authorized CBVs, stub
templates, and E006–E008 checks.

## Dependency

Pin `django-trusts` only, at kernel merge
`4d09045db0094cc40811af87c7783fa8f85acd90` or a later reviewed `master`
head. Never `django-trusts-zero`.

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install -e .
```

```python
INSTALLED_APPS = [
    'trusts.apps.KernelConfig',
    'gh_permissions.apps.GhPermissionsConfig',
]
AUTHENTICATION_BACKENDS = (
    'trusts.backends.ObjectAuthorizationBackend',
)
```

`GhPermissionsConfig.ready()` registers the process-wide identity
Context and the team/direct Trustee adapters. The requester model is
`Account` (not Django `User`). Views and admin treat `request.user` as
that account.

## Bounded policy

- `Account` membership in an `Organization` (`account.organizations`)
- `Account` membership in a `Team` (`account.teams`); teams belong to
  an organization
- `Repository` belongs to an organization
- A `Team` can receive a repository-scoped `PermissionBundle`
- An `Account` may receive a direct `AccountRepoGrant`
- Complete paths OR-compose; a partial membership or attachment grants
  nothing
- Team grants are capped by
  `alignment_paths=(('team__organization', 'repository__organization'),)`
- Revocation and malformed configuration fail closed

Public relations used to authorize: `account.teams`,
`team.permission_bundles`, `repository.organization`. There is no
`repository.policy` table: the repository row **is** the Trustee scope
(`Context.register_identity`).

## Usage

```python
from trusts.runtime import filter_authorized, is_authorized

is_authorized(account, 'read', repository)
Repository.objects.authorized(account, 'read')
filter_authorized(Repository.objects.all(), account, 'write')
```

Admin and CBVs set operation data only:

```python
class RepositoryAdmin(AuthorizedModelAdmin):
    list_operation = 'read'
    view_operation = 'read'
    change_operation = 'write'
    delete_operation = 'admin'
```

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

## Code-budget inventory

Counted as physical lines in this tree (generated `0001_initial` is listed with models).

| Category | Lines/files | Why consumer-owned |
|---|---:|---|
| Domain models | 137 + 98 generated migration / 2 files | Account, Organization, Team, Repository, PermissionBundle, Operation, TeamRepoGrant, AccountRepoGrant |
| Policy registrations | 57 / 1 file (`policy.py`; `apps.ready` calls it) | `register_identity` + `configure(..., operation_lookup='code')` + team/direct `register` + `alignment_paths` |
| Policy-specific overrides | 62 / 3 files (`admin.py`, `views.py`, `urls.py`) | Operation strings and URLconf on framework admin/CBV/decorator; `Repository.verbose_name` |
| Framework glue copied locally | **0** | S1–S5 own exists/list, backend, decorator, admin/CBVs/stubs, E006–E008. No local `grant_q`, backend, or check copy. |
| Tests/fixtures/docs | 810 / 10 files | Acceptance matrix, unsupported list, this inventory |

Package version is **0.1.0.dev0**.
