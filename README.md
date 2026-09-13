# django-trusts-gh-permissions

`django-trusts-gh-permissions` is a **bounded reference implementation**
that shows how permissions can emerge from persisted user,
organization, team, and repository relationships on
[django-trusts](https://github.com/django-trusts/django-trusts) 1.x.

It is **not affiliated with GitHub** and is **not** a complete GitHub
authorization clone. Treat it as a worked example, not a migration target.

Do **not** list `'trusts'` in `INSTALLED_APPS`. Core is a Python
library, not a Django app.

## Persisted graph

Authorization is compiled from stored rows. A complete path needs:

- user membership in a team
- team ownership by an organization
- repository ownership by an organization
- a team/repository permission plus a team operation ceiling
- optionally, a direct user/repository permission

Organization alignment and the team's allowed operations constrain a
complete team path. Direct and team paths OR together. Deleting any
required persisted relationship removes that path. Malformed,
incomplete, or revoked paths fail closed.

Organization membership is not stored here and is not a Trusts grant
edge. Team-membership consistency is application-validated domain data.

Requester, team-member, and direct-grant relations use
`settings.AUTH_USER_MODEL`.

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

`GhPermissionsConfig` owns the implementation. The accepted team
registration (contributed at startup) is:

```python
from trusts.core import All, Equal, permission_in

from gh_permissions.models import TeamRepositoryPermission

handle.register(
    TeamRepositoryPermission,
    user="team__members",
    permission="operation",
    content="repository",
    condition=All(
        permission_in("team__allowed_operations"),
        Equal("team__organization", "repository__organization"),
    ),
)
```

The optional direct user/repository grant is the three-FK
`UserRepositoryPermission` relation registered beside that team path.

## Authorize

Object decisions and authorized listings share the same compiled
policy. Callers pass the configured user model and `Operation`
instances. The object call uses the supported owner/registry lookup:

```python
from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import Repository
from trusts.apps import implementation_for_path

registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
    CANONICAL_BACKEND,
).registry
registry.has_permission(user, repository, operation)
Repository.objects.authorized(user, operation)
```

`Repository.objects` is the stock core `AuthorizedManager`.

## Install

This is a development reference implementation, not a declared stable
1.0, and not a published PyPI release. `django-trusts` 1.x is also
unpublished, so a clean environment cannot resolve it from PyPI.
Install a local core checkout or artifact first, then this package:

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install ../django-trusts
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
