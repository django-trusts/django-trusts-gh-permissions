# django-trusts-gh-permissions

`django-trusts-gh-permissions` is a **bounded reference
implementation** showing how repository permissions can emerge from
persisted account, organization, team, and repository relationships on
`django-trusts` 1.x.

It is a worked example, not a migration target and not a complete
clone of any external authorization product. It is **not affiliated with GitHub**.

Core is a Python library. Do **not** list `'trusts'` in
`INSTALLED_APPS`. This package owns `GhPermissionsConfig`, the
backend path, models, and persisted policy facts.

## Persisted graph

Authorization is compiled from stored rows:

- an `Account` may belong to an `Organization`
- an `Account` may belong to a `Team`
- a `Team` is owned by an `Organization`
- a `Repository` is owned by an `Organization`
- a team/repository grant carries an `Operation` and is capped by the
  team's permission-bundle membership
- an account may also hold a direct account/repository grant

Organization alignment and bundle membership must both hold for a
complete team path. Direct and team paths **OR** together. Deleting
any required relationship removes that path. Malformed, incomplete, or
revoked paths fail closed.

## Install

This tree is a development release, not a published PyPI package.
Install Django, `django-trusts` 1.x, then this checkout or a wheel
built from it:

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install "django-trusts>=1,<2"
python -m pip install .
```

Requires **Python 3.12–3.14** and **Django 6.1**.

## Configure

These settings match `tests/settings.py`:

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

`GhPermissionsConfig.ready()` contributes `register_direct` then the
accepted team `Ref` on the owner registry after `super().ready()`.

```python
from trusts.core import All, Equal, Ref, permission_in

from gh_permissions.models import TeamRepoGrant

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

That spelling is `gh_permissions.policy.register_team`. The direct
account/repository relation is `register_direct`. Callers pass
`Account` and `Operation` instances, not Django permission strings.

## Authorize

Object checks and authorized listings share the same compiled policy:

```python
from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import Repository
from trusts.apps import implementation_for_path

registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
    CANONICAL_BACKEND,
).registry
registry.has_permission(account, repository, operation)
Repository.objects.authorized(account, operation)
```

Grant mutation is ordinary ORM work on `AccountRepoGrant`,
`TeamRepoGrant`, membership, and bundle rows.

## Intentionally unsupported

There is no implicit permission-level hierarchy, org-owner admin,
public anonymous read, nested teams, invitations, token/app scopes,
branch protection, deploy keys, Actions secrets, forks, CODEOWNERS,
visibility matrix, or org default repository permission.

## Documentation

- [migrates.md](migrates.md) — settings and identity checklist
- [django-trusts](https://github.com/django-trusts/django-trusts) — core library
- [Issues](https://github.com/django-trusts/django-trusts-gh-permissions/issues)

Contributor and build history lives in [DEV.md](DEV.md).

Copyright BeeDesk, Inc., 2026 (BSD-2-Clause).
