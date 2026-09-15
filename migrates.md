# migrates.md — django-trusts-gh-permissions contributor checklist

This file is the mechanical checklist for the GH-owned
`GhPermissionsConfig` settings cutover and the #12 consumer-model
cleanup. It is **not** a user migration product and does **not**
describe a path from GitHub.com. It does **not** implement core
tombstones, Zero changes, examples, or Windows.

Implemented revision: bounded GH consumer-model cleanup for
[django-trusts-gh-permissions#12](https://github.com/django-trusts/django-trusts-gh-permissions/issues/12)
against the owner-only install from
[django-trusts-gh-permissions#6](https://github.com/django-trusts/django-trusts-gh-permissions/issues/6)
and documentation alignment in
[django-trusts-gh-permissions#9](https://github.com/django-trusts/django-trusts-gh-permissions/issues/9).

This is an unpublished development reference. **#12 is a clean-database
schema reset.** Do not invent an `Account.name` → `AUTH_USER_MODEL`
mapping. Drop the old tables and start from `gh_permissions.0001_initial`
as regenerated in this revision.

## Companion

| Item | Value |
| --- | --- |
| GH package | `0.1.0.dev0` (unchanged) |
| Core requirement | `django-trusts>=1.0.0.dev3,<2` |
| GH baseline (before #12) | merge [`eab54b8ba2a36434b93041ec9b53872a183bba5d`](https://github.com/django-trusts/django-trusts-gh-permissions/commit/eab54b8ba2a36434b93041ec9b53872a183bba5d) |
| Paired final core | [django-trusts#116](https://github.com/django-trusts/django-trusts/pull/116) merge [`1e19b5d464c067186aada58943c3ee67c44b2aa0`](https://github.com/django-trusts/django-trusts/commit/1e19b5d464c067186aada58943c3ee67c44b2aa0) (library cut [django-trusts#112](https://github.com/django-trusts/django-trusts/pull/112) `11058641`, `django-trusts==1.0.0.dev3`) |
| Zero | **absent** (not a dependency) |

Earlier GH snapshots paired with Step I `django-trusts==1.0.0.dev2`
(merge `39f1f961`). That pin is superseded. Final core deleted
`kernel_config()`, the core `AppConfig`, and
`trusts.backends.TrustModelBackend`.

## Public changes

| Surface | Old (merged `eab54b8`) | New (#12) |
| --- | --- |
| Requester / member / direct-grant subject | `Account` (`name`) | `settings.AUTH_USER_MODEL` (tests/fixtures: `get_user_model()`) |
| Direct permission row | `AccountRepoGrant(account, repository, operation)` | `UserRepositoryPermission(user, repository, operation)` |
| Team permission row | `TeamRepoGrant(team, repository, operation)` | `TeamRepositoryPermission(team, repository, operation)` |
| Team operation ceiling | `PermissionBundle(team, name, operations)` unioned by `permission_in(t.team.permission_bundles.operations)` | `Team.allowed_operations` M2M; `permission_in(t.team.allowed_operations)` |
| Organization membership | unused `Organization.members` M2M (`account.organizations`) | **removed** — not a Trusts grant edge |
| List manager | `GhAuthorizedQuerySet` / `GhAuthorizedManager` calling `implementation_for_path` | stock `trusts.query.AuthorizedManager` via `configured_implementation_handles()` |
| Core pin | `django-trusts>=1.0.0.dev3,<2` | **unchanged** |
| `INSTALLED_APPS` | `'gh_permissions.apps.GhPermissionsConfig'` only (no `'trusts'`) | **unchanged** |
| Registry owner | `implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')` | **unchanged** |
| Backend | `gh_permissions.backends.GhAuthorizationBackend` | **unchanged** mixin-only path |
| Models / migrations | `gh_permissions.0001_initial` (Account / bundles / grants) | **regenerated** `gh_permissions.0001_initial` (clean-DB reset) |
| Tables / content types | `gh_permissions_account`, `…permissionbundle`, `…accountrepogrant`, `…teamrepogrant`, org-members M2M | those tables **gone**; `…userrepositorypermission`, `…teamrepositorypermission`, `Team.allowed_operations` M2M |
| Authorization API | model instances (`Account`, `Operation`) | model instances (`AUTH_USER_MODEL`, `Operation`) |

### Old / new models

```python
# Old (eab54b8)
class Account(models.Model):
    name = models.CharField(max_length=40, unique=True)

class Organization(models.Model):
    members = models.ManyToManyField(Account, related_name='organizations')

class PermissionBundle(models.Model):
    team = models.ForeignKey(Team, related_name='permission_bundles')
    name = models.CharField(max_length=40)
    operations = models.ManyToManyField(Operation, related_name='bundles')

class AccountRepoGrant(models.Model):
    account = models.ForeignKey(Account, ...)
    repository = models.ForeignKey(Repository, ...)
    operation = models.ForeignKey(Operation, ...)

class TeamRepoGrant(models.Model):
    team = models.ForeignKey(Team, ...)
    repository = models.ForeignKey(Repository, ...)
    operation = models.ForeignKey(Operation, ...)

# New (#12)
# No Account. No PermissionBundle. No Organization.members.
class Team(models.Model):
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='teams')
    allowed_operations = models.ManyToManyField(Operation, related_name='allowed_teams')

class UserRepositoryPermission(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, ...)
    repository = models.ForeignKey(Repository, ...)
    operation = models.ForeignKey(Operation, ...)

class TeamRepositoryPermission(models.Model):
    team = models.ForeignKey(Team, ...)
    repository = models.ForeignKey(Repository, ...)
    operation = models.ForeignKey(Operation, ...)
```

### Old / new methods

```python
# Old listing glue (deleted)
class GhAuthorizedQuerySet(AuthorizedQuerySet):
    def authorized(self, user, permission, extra_q=None):
        granted(
            implementation_for_path(CANONICAL_BACKEND).configured_handles(),
            ...
        )
Repository.objects = GhAuthorizedManager()

# New (stock core)
from trusts.query import AuthorizedManager
Repository.objects = AuthorizedManager()
# AuthorizedQuerySet.authorized() reads configured_implementation_handles()
```

```python
# Old team condition
permission_in(t.team.permission_bundles.operations)

# New team condition
permission_in(t.team.allowed_operations)
```

```python
# Old donations (removed core helper; still forbidden)
from trusts.apps import kernel_config
kernel_config().configured_backend().registry

# Supported donations (unchanged)
from trusts.apps import implementation_for_path
implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')
```

### Startup / failure

| Situation | Supported behavior |
| --- | --- |
| Supported settings (`GhPermissionsConfig` + mixin backend) | populate succeeds; `GhPermissionsConfig` is the sole `TrustsImplementationConfig` |
| `'trusts'` listed | not the supported install (core ships no Django app) |
| Canonical backend path missing | `ImproperlyConfigured` from `_validate_ownership` (no silent return) |
| Core below `1.0.0.dev3` / missing helper | `ImproperlyConfigured` at import / ready |
| `kernel_config()` under supported final core | **not importable** |
| Existing GH database from `eab54b8` | **not migrated**; reset the database and apply regenerated `0001_initial` |

## Unchanged identity

- App label `gh_permissions`
- Migration key `('gh_permissions', '0001_initial')` (contents regenerated)
- Domain `Operation` (not Django `auth.Permission`)
- Repository-scoped team permission row (renamed, still three FKs)
- Two separately reviewable atoms: `register_direct` then `register_team`
- Independent-root OR, fail-closed, fixed query counts
- Mixin-only backend; generic `PlanQueryCompiler`; no Zero historical compiler
- Separate `policy.py` from `apps.py`

`makemigrations gh_permissions --check` is quiet on a tree that already
has the regenerated `0001_initial`. Already-applied pre-#12 GH DBs do
**not** keep matching tables; reset them.

## Contributor checklist

Search application code and settings for:

```text
INSTALLED_APPS.*trusts
from trusts.apps import kernel_config
kernel_config(
from trusts.core_backends
from trusts.zero
django-trusts-zero
register_gh_policy
class Account
AccountRepoGrant
TeamRepoGrant
PermissionBundle
permission_bundles
GhAuthorizedQuerySet
GhAuthorizedManager
Organization.members
account.organizations
```

Then:

- [ ] Remove `'trusts'` from `INSTALLED_APPS`. Keep `'gh_permissions.apps.GhPermissionsConfig'` (or `'gh_permissions'` with `default=True`).
- [ ] Keep `'gh_permissions.backends.GhAuthorizationBackend'`. Do not list `'trusts.backends.TrustModelBackend'`.
- [ ] Replace `kernel_config()` donations with `implementation_for_path('gh_permissions.backends.GhAuthorizationBackend')`.
- [ ] Confirm `ready()` no longer returns silently when the owner/path is missing.
- [ ] Pin `django-trusts>=1.0.0.dev3,<2`. Pair CI uses merge `1e19b5d`.
- [ ] Do not add `django-trusts-zero`. Do not import `trusts.zero` or `trusts.core_backends`.
- [ ] Keep `register_direct` and `register_team` as separate functions. Do not add `register_gh_policy()`.
- [ ] Replace `Account` FKs with `settings.AUTH_USER_MODEL`. Tests/fixtures use `get_user_model()`.
- [ ] Replace `AccountRepoGrant` with `UserRepositoryPermission` and `TeamRepoGrant` with `TeamRepositoryPermission`.
- [ ] Flatten `PermissionBundle` into `Team.allowed_operations`. Update the team condition to `permission_in(t.team.allowed_operations)`.
- [ ] Delete `GhAuthorizedQuerySet` / `GhAuthorizedManager`. Keep `Repository.objects = AuthorizedManager()` from `trusts.query`.
- [ ] Remove unused `Organization.members`. Do not describe org membership as a Trusts grant edge.
- [ ] Do **not** write an `Account.name` → User data migration. Reset the database.
- [ ] `python -m django migrate --plan` — apply regenerated `gh_permissions.0001_initial` on a clean database.
- [ ] `python -m django makemigrations gh_permissions --check` — quiet.
- [ ] Confirm object / list / enumeration still OR both roots and fail closed with the same query counts.
- [ ] Confirm the stock `AuthorizedManager` reaches this implementation's registered handles via `configured_implementation_handles()`.
- [ ] Leave package version at `0.1.0.dev0`.
- [ ] Do not add user-owned repositories or `Organization.owner` authorization.
- [ ] Do not fold `policy.py` into `apps.py`.
- [ ] Do not begin Windows #17 or examples.

## Out of scope

- Windows conversion / README
- Examples
- RST sweep
- User-owned repositories
- Organization.owner / org-owner admin authorization
- Folding `policy.py` into `apps.py`
- A data-preserving Account → User mapping
- A user migration API

# Adopt handle.register pair (issue #131)

This record is the **executable G1 delta** on live `main`
`600ae3746f3934b3d25417c7201cbf772dbe4ee4`. Historical sections above
stay as written, including the #12 AUTH_USER_MODEL / flatten-bundles /
stock `AuthorizedManager` reset and the IIb owner cutover. Those
describe earlier stairs. The stale initial-only `dev` branch is not
this implementation baseline.

Authorization **data**, independent-root OR, organization equality,
team operation ceiling, fail-closed incomplete/revoked paths,
object/list/enumeration behavior, fixed query counts,
`AUTH_USER_MODEL`, stock `AuthorizedManager`, and
`gh_permissions.0001_initial` are unchanged. No Trusts schema
migration is added. Package version stays `0.1.0.dev0`. Zero stays
absent.

## Pin

| | |
| --- | --- |
| Previous | Core [#116](https://github.com/django-trusts/django-trusts/pull/116) merge `1e19b5d464c067186aada58943c3ee67c44b2aa0` (library cut [#112](https://github.com/django-trusts/django-trusts/pull/112) `11058641`, `django-trusts==1.0.0.dev3`). |
| New | Core `handle.register` API at `e9fd4cd4f77624f3d5351b505808c1d6fa8bcbc4` (merged [django-trusts#158](https://github.com/django-trusts/django-trusts/pull/158)). Floor remains `django-trusts>=1.0.0.dev3,<2`. |
| Replacement | Same git URL, new exact SHA in `requirements.txt`, `scripts/django-trusts.pin`, CI `COMPANION_KERNEL_SHA`, and package-metadata needles. No floating branch. Never `django-trusts-zero`. |
| Affected | Package install, policy donation, pin-integrity constants, DEV pairing line. |
| Authorization | Same allow/deny. Direct and team roots still OR; only the registration argument changes. |

## Old → new

```python
# Old (#12 on 1e19b5d)
from trusts.core import All, Equal, Ref, permission_in

def register_direct(registry):
    d = Ref(UserRepositoryPermission)
    return registry.register(
        content=d.repository, user=d.user, permission=d.operation,
    )

def register_team(registry):
    t = Ref(TeamRepositoryPermission)
    return registry.register(
        content=t.repository,
        user=t.team.members,
        permission=t.operation,
        condition=All(
            permission_in(t.team.allowed_operations),
            Equal(t.team.organization, t.repository.organization),
        ),
    )

registry = owner.configured_backend(CANONICAL_BACKEND).registry
if getattr(self, '_gh_policy_registry_id', None) is registry:
    return
register_direct(registry)
register_team(registry)
self._gh_policy_registry_id = registry

# New (paired e9fd4cd4)
from trusts.core import All, Equal, permission_in

def register_direct(handle):
    return handle.register(
        UserRepositoryPermission,
        user='user',
        permission='operation',
        content='repository',
    )

def register_team(handle):
    return handle.register(
        TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=All(
            permission_in('team__allowed_operations'),
            Equal('team__organization', 'repository__organization'),
        ),
    )

handle = owner.configured_backend(CANONICAL_BACKEND)
register_direct(handle)
register_team(handle)
```

Helpers require a `BackendHandle` (`TypeError` on a bare registry).
Exact-duplicate registration still fails closed without mutating stored
records. Invalid configuration fails before either helper runs. Do not
store `handle.registry` for re-entry. Do not call `.registry.register`
from production donation. `handle.registry` remains temporarily so
compiler tests can inspect normalized records and so object/list
evaluation can keep `registry.has_permission` / `.authorized()`.

## Migration-bot checklist

Search application code, README, DEV/reference snippets, and
registration fixtures for:

```text
from trusts.core import Ref
Ref(
.registry.register(
1e19b5d464c067186aada58943c3ee67c44b2aa0
1e19b5d
from trusts.zero
import trusts.zero
django-trusts-zero
```

Then:

- [ ] Pin Core `e9fd4cd4f77624f3d5351b505808c1d6fa8bcbc4` in `requirements.txt`, `scripts/django-trusts.pin`, and CI `COMPANION_KERNEL_SHA`.
- [ ] Retarget the package-metadata / README needle from `1e19b5d` to `e9fd4cd4`.
- [ ] Convert `register_direct` / `register_team` to take the configured handle and call `handle.register` with Django `__` paths.
- [ ] Convert `GhPermissionsConfig.ready()` to donate both roots on the handle. Do not key idempotency on `.registry`.
- [ ] Remove consumer-facing `Ref` and `.registry.register(` from production, README, DEV/reference snippets, and registration fixtures.
- [ ] Keep isolated compiler fail-closed proofs that call `TrustsRegistry.register` with `Ref` for malformed paths.
- [ ] Keep evaluation `registry.has_permission` / `Repository.objects.authorized`.
- [ ] Keep `register_direct` and `register_team` as separate functions. Do not add `register_gh_policy()`.
- [ ] Keep Zero absent. Do not import `trusts.zero`.
- [ ] Leave package version at `0.1.0.dev0`. Do not change schema/models.
- [ ] Target `main` (`600ae374…`). Do not replace or regress from stale `dev`.
- [ ] `python -m tests.runtests` on Python 3.12–3.14.
- [ ] Fresh `migrate --noinput` + `check` + `makemigrations gh_permissions --check`.
- [ ] Pair job and package/wheel metadata against the exact SHA.
- [ ] Do not start C1-fold `handle.register_strategy` / `OrderedFold`, W1, C2 (`handle.registry` removal), #146, #159/#160, or release/version work.

# Adopt handle.register_relationship pair (issue #16)

This record is the **executable G2 delta** on live `main`
`98e6db7b04d81370cd75a9c291ead8f83f519dd3`. Historical sections above
stay as written, including the G1 `handle.register` pair on
`e9fd4cd4…`, the #12 AUTH_USER_MODEL / flatten-bundles / stock
`AuthorizedManager` reset, and the IIb owner cutover. Those describe
earlier stairs. Do not rewrite them as though they used the final
`register_relationship` spelling.

Authorization **data**, independent-root OR, organization equality,
team operation ceiling, fail-closed incomplete/revoked paths,
object/list/enumeration behavior, fixed query counts,
`AUTH_USER_MODEL`, stock `AuthorizedManager`, and
`gh_permissions.0001_initial` are unchanged. No Trusts schema
migration is added. Package version stays `0.1.0.dev0`. Zero stays
absent.

## Pin

| | |
| --- | --- |
| Previous | Core `handle.register` API at `e9fd4cd4f77624f3d5351b505808c1d6fa8bcbc4` (merged [django-trusts#158](https://github.com/django-trusts/django-trusts/pull/158)). Floor remains `django-trusts>=1.0.0.dev3,<2`. |
| New | Core `handle.register_relationship` API at `bc25cd9524b12cd15047a401d12b82f813b6e500` (merged [django-trusts#174](https://github.com/django-trusts/django-trusts/pull/174), which removed the temporary `BackendHandle.register(...)` forwarder). Floor remains `django-trusts>=1.0.0.dev3,<2`. |
| Replacement | Same git URL, new exact SHA in `requirements.txt`, `scripts/django-trusts.pin`, CI `COMPANION_KERNEL_SHA`, and package-metadata needles. No floating branch. Never `django-trusts-zero`. |
| Affected | Package install, policy donation, pin-integrity constants, DEV pairing line, README / test fixtures that taught `handle.register`. |
| Authorization | Same allow/deny. Direct and team roots still OR; only the registration method name changes. Persisted schema and data are unchanged. |

## Old → new

```python
# Old (G1 on e9fd4cd4)
from trusts.core import All, Equal, permission_in

def register_direct(handle):
    return handle.register(
        UserRepositoryPermission,
        user='user',
        permission='operation',
        content='repository',
    )

def register_team(handle):
    return handle.register(
        TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=All(
            permission_in('team__allowed_operations'),
            Equal('team__organization', 'repository__organization'),
        ),
    )

handle = owner.configured_backend(CANONICAL_BACKEND)
register_direct(handle)
register_team(handle)

# New (paired bc25cd95)
from trusts.core import All, Equal, permission_in

def register_direct(handle):
    return handle.register_relationship(
        UserRepositoryPermission,
        user='user',
        permission='operation',
        content='repository',
    )

def register_team(handle):
    return handle.register_relationship(
        TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=All(
            permission_in('team__allowed_operations'),
            Equal('team__organization', 'repository__organization'),
        ),
    )

handle = owner.configured_backend(CANONICAL_BACKEND)
register_direct(handle)
register_team(handle)
```

Helpers still require a `BackendHandle` (`TypeError` on a bare registry).
Exact-duplicate registration still fails closed without mutating stored
records. Invalid configuration fails before either helper runs. Keep
`register_direct` and `register_team` as separate atoms. Do not add
`register_gh_policy()` or any other aggregate helper.

Do not introduce `register_ordered_fold`, private registry donation,
`Ref`, or a Zero dependency. Evaluation still uses
`registry.has_permission` / `Repository.objects.authorized`. Isolated
compiler fail-closed proofs may still call `TrustsRegistry.register`
with `Ref` for malformed paths.

## Migration-bot checklist

Search application code, README, DEV/reference snippets, and
registration fixtures for:

```text
handle.register(
e9fd4cd4f77624f3d5351b505808c1d6fa8bcbc4
e9fd4cd4
register_ordered_fold
from trusts.core import Ref
Ref(
.registry.register(
from trusts.zero
import trusts.zero
django-trusts-zero
register_gh_policy
```

Treat historical `migrates.md` G1 / #12 stairs as labeled history.
Current production, README, DEV pairing, and executable fixtures must
not teach `handle.register(`.

Then:

- [ ] Search code and docs for leftover production / user-facing `handle.register(`.
- [ ] Pin Core `bc25cd9524b12cd15047a401d12b82f813b6e500` in `requirements.txt`, `scripts/django-trusts.pin`, and CI `COMPANION_KERNEL_SHA`.
- [ ] Retarget the package-metadata / README needle from `e9fd4cd4` to `bc25cd95`.
- [ ] Convert `register_direct` / `register_team` to call `handle.register_relationship` with the same Django `__` paths.
- [ ] Keep `register_direct` and `register_team` as separate functions. Do not add `register_gh_policy()`.
- [ ] Startup still donates both independent roots on the configured handle.
- [ ] Keep Zero absent. Do not import `trusts.zero`.
- [ ] Leave package version at `0.1.0.dev0`. Do not change schema/models/migrations.
- [ ] `python -m django migrate --noinput` + `check` + `makemigrations gh_permissions --check` remain quiet.
- [ ] Confirm object / list / enumeration still OR both roots and fail closed with the same query counts.
- [ ] `python -m tests.runtests` on Python 3.12–3.14.
- [ ] Pair job and package/wheel metadata against the exact SHA.
- [ ] Do not start Core #132 / #137. Do not introduce `register_ordered_fold`, private registry donation, `Ref`, or Zero.

# Adopt register() and symbolic condition (issue #18)

This record is the **executable G3 delta** on live `main`
`25018b05c02b0856e60033d2f29aef5629e10195`. Historical sections above
stay as written, including the G2 `register_relationship` pair on
`bc25cd95…`, the G1 temporary `handle.register` forwarder on
`e9fd4cd4…`, the #12 AUTH_USER_MODEL / flatten-bundles / stock
`AuthorizedManager` reset, and the IIb owner cutover. Those describe
earlier stairs. Do not rewrite them as though they used the final
`register(trust=..., condition=lambda t: ...)` spelling.

Authorization **data**, independent-root OR, organization equality,
team operation ceiling, fail-closed incomplete/revoked paths,
object/list/enumeration behavior, fixed query counts,
`AUTH_USER_MODEL`, stock `AuthorizedManager`, and
`gh_permissions.0001_initial` are unchanged. No Trusts schema
migration is added. Package version stays `0.1.0.dev0`. Zero stays
absent. Literal Python `in` stays unsupported.

## Pin

| | |
| --- | --- |
| Previous | Core `handle.register_relationship` API at `bc25cd9524b12cd15047a401d12b82f813b6e500` (merged [django-trusts#174](https://github.com/django-trusts/django-trusts/pull/174)). Floor remains `django-trusts>=1.0.0.dev3,<2`. |
| New | Core public `BackendHandle.register` plus symbolic-condition API at `8bfe6151b5a65af2d0667ab3a71680eecc90a691` ([django-trusts#211](https://github.com/django-trusts/django-trusts/pull/211) approved head, stacked on [django-trusts#208](https://github.com/django-trusts/django-trusts/pull/208) `15e8fa80c1184ae338078e967dfd60dcdd2b67cc`). Floor remains `django-trusts>=1.0.0.dev3,<2`. |
| Replacement | Same git URL, new exact SHA in `requirements.txt`, `scripts/django-trusts.pin`, CI `COMPANION_KERNEL_SHA`, and package-metadata needles. No floating branch. Never `django-trusts-zero`. |
| Affected | Package install, policy donation, pin-integrity constants, DEV pairing line, README / test fixtures that taught `register_relationship` plus public `All` / `permission_in` / `Equal`. |
| Authorization | Same allow/deny. Direct and team roots still OR; only the registration method and public condition grammar change. Persisted schema and data are unchanged. |

## Old → new

```python
# Old (G2 on bc25cd95)
from trusts.core import All, Equal, permission_in

def register_direct(handle):
    return handle.register_relationship(
        UserRepositoryPermission,
        user='user',
        permission='operation',
        content='repository',
    )

def register_team(handle):
    return handle.register_relationship(
        TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=All(
            permission_in('team__allowed_operations'),
            Equal('team__organization', 'repository__organization'),
        ),
    )

handle = owner.configured_backend(CANONICAL_BACKEND)
register_direct(handle)
register_team(handle)

# New (paired 8bfe6151)
def register_direct(handle):
    return handle.register(
        trust=UserRepositoryPermission,
        user='user',
        permission='operation',
        content='repository',
    )

def register_team(handle):
    return handle.register(
        trust=TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=lambda t: (
            t.team.allowed_operations.contains(t.operation)
            & (t.team.organization == t.repository.organization)
        ),
    )

handle = owner.configured_backend(CANONICAL_BACKEND)
register_direct(handle)
register_team(handle)
```

The public condition grammar is exactly `.contains(...)`, `==`, and
`&`. The ceiling and organization equality stay AND-combined on the
one team registration. The predicate runs once after the freeze check.
Stored policy is private normalized `All` / `PermissionIn` / `Equal`
IR and contains no application callable.

Helpers still require a `BackendHandle` (`TypeError` on a bare registry).
Exact-duplicate registration still fails closed without mutating stored
records. Invalid configuration fails before either helper runs. Keep
`register_direct` and `register_team` as separate atoms. Do not add
`register_gh_policy()` or any other aggregate helper. Do not add
`predicate=`. Do not use public `All`, `Equal`, or `permission_in`.
Do not write literal Python `in`.

Do not introduce `register_ordered_fold`, private registry donation,
`Ref`, reverse walking, new joins, schema changes, or a Zero
dependency. Evaluation still uses `registry.has_permission` /
`Repository.objects.authorized`. Isolated compiler fail-closed proofs
may still call `TrustsRegistry.register` with `Ref` for malformed
paths, and tests may inspect stored private IR.

## Migration-bot checklist

Search application code, README, DEV/reference snippets, and
registration fixtures for:

```text
register_relationship(
permission_in(
Equal(
All(
handle.register_relationship(
from trusts.core import All, Equal, permission_in
bc25cd9524b12cd15047a401d12b82f813b6e500
bc25cd95
predicate=
 in 
register_ordered_fold
from trusts.core import Ref
Ref(
.registry.register(
from trusts.zero
import trusts.zero
django-trusts-zero
register_gh_policy
```

Treat historical `migrates.md` G2 / G1 / #12 stairs as labeled
history. Current production, README, DEV pairing, and executable
fixtures must not teach `register_relationship(`, public
`permission_in(`, public `Equal(`, or public `All(` as the live
application-facing policy. Literal Python `in` stays unsupported.

Then:

- [ ] Search current application-facing policy for leftover
      `register_relationship(`, `permission_in(`, `Equal(`, and `All(`.
- [ ] Pin Core `8bfe6151b5a65af2d0667ab3a71680eecc90a691` in
      `requirements.txt`, `scripts/django-trusts.pin`, and CI
      `COMPANION_KERNEL_SHA`.
- [ ] Retarget the package-metadata / README needle from `bc25cd95`
      to `8bfe615`.
- [ ] Convert `register_direct` / `register_team` to call
      `handle.register(trust=..., ...)` with the same Django `__`
      path strings.
- [ ] Convert the team condition to
      `lambda t: (t.team.allowed_operations.contains(t.operation) & (t.team.organization == t.repository.organization))`.
- [ ] Keep `register_direct` and `register_team` as separate functions.
      Do not add `register_gh_policy()`.
- [ ] Startup still donates both independent roots on the configured
      handle. Donation remains idempotent and is designed not to issue
      SQL.
- [ ] Stored policy is private IR, not the application callable.
- [ ] Keep Zero absent. Do not import `trusts.zero`.
- [ ] Leave package version at `0.1.0.dev0`. Do not change
      schema/models/migrations.
- [ ] `python -m django migrate --noinput` + `check` +
      `makemigrations gh_permissions --check` remain quiet.
- [ ] Confirm object / list / enumeration still OR both roots and fail
      closed with the same query counts.
- [ ] With the local team permission record retained, removing the
      operation ceiling or mismatching organizations denies object,
      list, and enumeration.
- [ ] `python -m tests.runtests` on Python 3.12–3.14.
- [ ] Pair job and package/wheel metadata against the exact SHA.
- [ ] Do not modify or tip-chase django-trusts #206 / #208 / #211 or
      Zero #38. Do not introduce `register_ordered_fold`, private
      registry donation, `Ref`, reverse walking, new joins, or Zero.
