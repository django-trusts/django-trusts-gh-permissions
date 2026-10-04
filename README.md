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
    'example.apps.ExampleConfig',
    'gh_permissions.apps.GhPermissionsConfig',
)
AUTH_USER_MODEL = 'example.User'

AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
)
```

`example.User` is the test project's user. Its manager mixes in
`PermittedUsersManagerMixin`, which is what makes
`User.objects.permitted(content, perm)` available. The library models
use `settings.AUTH_USER_MODEL` and do not import `example`. A project
that keeps stock `auth.User` still uses `repository.get_permitted_users`.

`GhPermissionsConfig` owns the implementation. The accepted team
registration (contributed at startup) is:

```python
from gh_permissions.models import TeamRepositoryPermission

backend.register(
    trust=TeamRepositoryPermission,
    user="team__members",
    permission="operation",
    content="repository",
    condition=lambda t: (
        t.team.allowed_operations.contains(t.operation)
        & (t.team.organization == t.repository.organization)
    ),
)
```

The optional direct user/repository grant is the three-FK
`UserRepositoryPermission` relation registered beside that team path.

## Authorize

Object decisions and authorized listings share the same compiled
policy. The permission row is `auth.Permission`. Codename shape is
`gh_permissions.read_repository` and `gh_permissions.write_repository`
(and `gh_permissions.admin_repository`). `Repository.objects.authorized`
takes that permission instance. `has_perm` and `permitted` also accept
the Django permission string as the alias.

```python
from example.models import User
from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import Repository
from trusts.apps import implementation_for_path

registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
    CANONICAL_BACKEND,
).registry
registry.has_permission(user, repository, operation)
Repository.objects.authorized(user, operation)
user.has_perm('gh_permissions.read_repository', repository)
User.objects.permitted(repository, 'gh_permissions.read_repository')
repository.get_permitted_users('gh_permissions.read_repository')
```

`Repository.objects` is the stock core `AuthorizedManager`.
`Repository` mixes in `PermittedUsersMixin`. With the example user
manager, the same rows are `User.objects.permitted(repository, perm)`.

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
