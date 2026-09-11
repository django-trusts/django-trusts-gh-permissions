# django-trusts-gh-permissions

Minimal GH-style organization, team, and repository authorization built
on [`django-trusts`](https://github.com/django-trusts/django-trusts).
This package is an independent relational-policy example. It is **not**
affiliated with or endorsed by any external service and does not claim
complete compatibility.

Application authors own the domain models and compact
``TrustsRegistry.register(content=, user=, permission=, condition=)``
calls. Core owns validation, correlated query construction, authorization
control flow, and the fixed-query projections. Copied framework
authorization glue in this tree must stay zero.

## Dependency

Pin `django-trusts` only, at C2 merge
`db5a41ed66478b79e10b0a066c8c0bca8fbe7882`. Never `django-trusts-zero`.

```
python -m pip install "Django>=6.1,<6.2"
python -m pip install -e .
```

```python
INSTALLED_APPS = [
    'trusts',
    'gh_permissions',
]
```

GH-only startup does not list ``TrustModelBackend`` and does not import
Zero. Kernel ``AppConfig.label`` is ``trusts_core``. There is no
``trusts`` schema or ``trusts.*`` migration. ``import trusts.models``,
``dir()``, ``hasattr()``, and unknown attributes stay inert.

``GhPermissionsConfig.ready()`` registers the direct-account root on an
isolated ``TrustsRegistry``. The requester model is ``Account``.
Permission values are ``Operation`` instances
(``Operation.objects.get(code=...)``).

## Bounded policy

- ``Account`` membership in an ``Organization`` (``account.organizations``)
- ``Account`` membership in a ``Team`` (``account.teams``); teams belong
  to an organization
- ``Repository`` belongs to an organization
- A ``Team`` can receive a repository-scoped ``PermissionBundle``
- An ``Account`` may receive a direct ``AccountRepoGrant``
- Complete paths OR-compose; a partial membership or attachment grants
  nothing
- Team grants are capped by grant-row organization equality and the
  bundle/global ceiling
- Revocation and malformed configuration fail closed

Public relations used to authorize: ``account.teams``,
``team.permission_bundles``, ``repository.organization``.

Accepted registrations (r7/r8/r9):

```python
from trusts.core import All, Equal, Ref, permission_in
from gh_permissions.models import AccountRepoGrant, TeamRepoGrant

def register_gh(registry):
    d = Ref(AccountRepoGrant)
    registry.register(
        content=d.repository,
        user=d.account,
        permission=d.operation,
    )
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

``Repository.objects = AuthorizedManager()`` is instance-only
(``.authorized(account, operation_instance)``). There is no
``.permitted`` / ``.get_permission``.

## Missing-core report (G1 stop)

Live C2 at ``db5a41ed66478b79e10b0a066c8c0bca8fbe7882`` cannot express
the accepted team mapping. This repository does **not** add a
consumer-local escape dialect and does not widen core.

Verified on that exact SHA:

1. ``trusts.core`` does not export ``permission_in``, ``Equal``, or
   ``All``.
2. ``TrustsRegistry.register(..., condition=)`` raises
   ``TrustsConfigurationError('condition is not supported; omit it or
   pass None.')`` for any non-``None`` value
   (``trusts/core.py`` ``register``, live C2).
3. User paths remain one direct single-valued hop. ``t.team.members`` is
   a forward FK plus an M2M membership hop; M2M is rejected
   (``_resolve_path`` / ``_classify_field``).
4. Related: ``AuthorizedQuerySet.authorized`` reads
   ``kernel_config().configured_handles()``, which is keyed by
   ``AUTHENTICATION_BACKENDS`` mixin paths. GH must not list
   ``TrustModelBackend``, so the process-wide manager does not see the
   isolated GH registry. Isolated ``registry.has_permission`` /
   ``filter_authorized`` / ``permissions_for`` remain the public
   instance projections that work today.

``tests/test_missing_core.py`` is the failing minimal test: it calls
``register_team()`` with the accepted mapping and requires a stored
team record. Direct-account grants already compile and are covered by
``tests/test_direct.py``.

Required core additions before G1 can finish (already accepted in
r7/r8/r9; not to be implemented in this consumer):

- User-path grammar: after zero or more forward single-valued hops,
  allow one final membership hop (M2M or reverse O2M) that terminates
  on the requester model.
- Implement the shipped ``condition=`` slot with closed constructors
  ``permission_in(*refs)``, ``Equal(left, right)``, and ``All(*predicates)``.
- Zero-SQL validation of those predicates at ``register()`` / checks.
- A GH-only handle store that ``AuthorizedManager.authorized`` reads
  without ``TrustModelBackend`` or Django auth Permission strings.

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

Counted as physical lines in this tree (generated ``0001_initial`` is
listed with models).

| Category | Lines/files | Why consumer-owned |
|---|---:|---|
| Domain models | 137 + 98 generated migration / 2 files | Account, Organization, Team, Repository, PermissionBundle, Operation, TeamRepoGrant, AccountRepoGrant |
| Policy registrations | 47 + 27 / ``policy.py`` + ``apps.py`` | one C2 ``register()`` for the direct-account root; ``register_team`` is the accepted mapping held for core |
| Policy-specific overrides | **0** | C2 has no instance-only admin/CBV/decorator mixins; GH does not copy abandoned surfaces |
| Framework glue copied locally | **0** | no Context/Trustee/adapters/alignment compiler/backend/checks/query compiler |
| Tests/fixtures/docs | 617 tests + 160 README + packaging/CI | direct-path matrix, topology, fail-closed, missing-core stop, unsupported list |

Package version is **0.1.0.dev0**.
