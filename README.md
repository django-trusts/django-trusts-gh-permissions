# django-trusts-gh-permissions

`django-trusts-gh-permissions` is a **bounded reference implementation**
that shows how permissions can emerge from persisted account,
organization, team, and repository relationships on
[django-trusts](https://github.com/django-trusts/django-trusts) 1.x.

It is **not affiliated with GitHub** and is **not** a complete GitHub
authorization clone. Treat it as a worked example, not a migration target.

Do **not** list `'trusts'` in `INSTALLED_APPS`. Core is a Python
library, not a Django app.

## Persisted graph

Authorization is compiled from stored rows. A complete path needs:

- account membership in an organization
- account membership in a team
- team ownership by an organization
- repository ownership by an organization
- a team/repository grant plus a permission bundle/operation
- optionally, a direct account/repository grant

Organization alignment and bundle membership constrain a complete team
path. Direct and team paths OR together. Deleting any required
persisted relationship removes that path. Malformed, incomplete, or
revoked paths fail closed.

## Configure

These imports and settings match the project's verified test
configuration. Core `'trusts'` is absent.

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

`GhPermissionsConfig` owns the implementation. The accepted team `Ref`
registration (contributed at startup) is:

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

The optional direct account/repository grant is the three-FK
`AccountRepoGrant` relation registered beside that team path.

## Authorize

Object decisions and authorized listings share the same compiled
policy. Callers pass `Account` and `Operation` instances:

```python
from gh_permissions.models import Repository

Repository.objects.filter(pk=repository.pk).authorized(
    account, operation,
).exists()
Repository.objects.authorized(account, operation)
```

## Install

This is a development reference implementation, not a declared stable
1.0, and not a published PyPI release. Install from a local checkout
or a built sdist/wheel. `django-trusts` 1.x arrives as a dependency.

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install .
```

Requires **Python 3.12–3.14** and **Django 6.1**.

## Limitations

There is no implicit permission-level hierarchy, org-owner admin,
public anonymous read, nested teams, invitations, token/app scopes,
branch protection, deploy keys, Actions secrets, forks, CODEOWNERS,
visibility matrix, or org default repository permission.

## Documentation

- [django-trusts](https://github.com/django-trusts/django-trusts) — core library
- [Issues](https://github.com/django-trusts/django-trusts-gh-permissions/issues)

Contributor and build history lives in [DEV.md](DEV.md).

Copyright BeeDesk, Inc., 2026 (BSD-2-Clause).
