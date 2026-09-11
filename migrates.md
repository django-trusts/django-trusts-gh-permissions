# migrates.md — django-trusts-gh-permissions IIb (0.1.0.dev0)

This file is the mechanical checklist for Step IIb: GH-owned
`AppConfig` registry lifecycle. It closes
[django-trusts-gh-permissions#6](https://github.com/django-trusts/django-trusts-gh-permissions/issues/6).
Authorized by approved
[#102 r3](https://github.com/django-trusts/django-trusts/issues/102#issuecomment-5638914363),
[r4](https://github.com/django-trusts/django-trusts/issues/102#issuecomment-5638983164),
and [r5](https://github.com/django-trusts/django-trusts/issues/102#issuecomment-5639018377).
Core pair pin is [django-trusts#109](https://github.com/django-trusts/django-trusts/pull/109)
merge [`39f1f9611e214193aec4e97526cf9b54ee689967`](https://github.com/django-trusts/django-trusts/commit/39f1f9611e214193aec4e97526cf9b54ee689967)
(`django-trusts==1.0.0.dev2`). Zero IIa
[`94e0fa109a8a7a5f53a028438ada899cbc1be1ad`](https://github.com/django-trusts/django-trusts-zero/commit/94e0fa109a8a7a5f53a028438ada899cbc1be1ad)
is context only; this package must not depend on Zero.

Package version stays **0.1.0.dev0**.

## Companion kernel (IIb)

| Item | Value |
| --- | --- |
| Authoritative GH metadata | this repo `pyproject.toml` (`0.1.0.dev0` + `django-trusts>=1.0.0.dev2,<2`) |
| Paired Step I core | [django-trusts#109](https://github.com/django-trusts/django-trusts/pull/109) merge [`39f1f9611e214193aec4e97526cf9b54ee689967`](https://github.com/django-trusts/django-trusts/commit/39f1f9611e214193aec4e97526cf9b54ee689967) |
| GH baseline (G1) | [`9c9fbc69c5f6c3bfb672d6d585b20ecff7a475be`](https://github.com/django-trusts/django-trusts-gh-permissions/commit/9c9fbc69c5f6c3bfb672d6d585b20ecff7a475be) / raw pin [`595e2f9f0cc97f8744c1178f3e384dba5787773c`](https://github.com/django-trusts/django-trusts/commit/595e2f9f0cc97f8744c1178f3e384dba5787773c) |

## Public changes (IIb)

Stored schema and authorization **data** stay compatible. Public
**dependency, settings, and startup** change.

| Surface | Old (G1 / C2 + #98) | New (IIb / Step I) |
| --- | --- | --- |
| Distribution | `django-trusts-gh-permissions==0.1.0.dev0` + `django-trusts @ …@595e2f9f0cc97f8744c1178f3e384dba5787773c` | `django-trusts-gh-permissions==0.1.0.dev0` + `django-trusts>=1.0.0.dev2,<2` |
| `INSTALLED_APPS` | `'trusts'` then `'gh_permissions'` | **`'gh_permissions'` only** (no `'trusts'`) |
| Backend settings | `'gh_permissions.backends.GhAuthorizationBackend'` | **unchanged** |
| Owner API | `kernel_config()` swallow in `GhPermissionsConfig.ready()` | `GhPermissionsConfig(TrustsImplementationConfig)` + `implementation_for_path` / `gh_config()` |
| Models | `from gh_permissions.models import Account, Repository, …` | **unchanged** |
| Django app label | `gh_permissions` | **`gh_permissions`** (unchanged) |
| Migration name | `0001_initial` | **unchanged** loader key `gh_permissions.0001_initial` |
| Tables / content types / rows | `gh_permissions_repository`, `gh_permissions \| repository`, … | **unchanged** |

### Old / new startup and failure behavior

| Situation | Old (G1) | New (IIb) |
| --- | --- | --- |
| Canonical IIb settings | N/A | Starts. No core `AppConfig`. Exactly one implementation owner (`GhPermissionsConfig`). |
| `'trusts'` in `INSTALLED_APPS` | Required (kernel store) | Not required. Supported IIb omits it. |
| Canonical path missing | `ready()` swallowed `LookupError` / `TrustsConfigurationError` and skipped donation | **`ImproperlyConfigured`** from the public Step I lifecycle. No silent return. |
| Core below `1.0.0.dev2` | Installable (git pin at #98) | **Metadata refuse** (`Requires-Dist: django-trusts>=1.0.0.dev2,<2`) and **startup belt** `ImproperlyConfigured` if `TrustsImplementationConfig` is missing |
| Missing kernel `AppConfig` | `GhPermissionsConfig.ready()` swallowed `LookupError` and skipped donation | **OK.** Donation writes GH's registry. The kernel accessor is never called. |
| Owner-present mixin / list / object / enumeration | Used `kernel_config()` | Resolves through `GhPermissionsConfig`. The kernel accessor is not called. |
| Duplicate owner / path | Silent skip or kernel store | **Fail loud** through `TrustsImplementationConfig.ready()` / `implementation_for_path` |

Do **not** add `'trusts'` back, call the kernel accessor from GH
package code, import `trusts.core_backends`, list the core historical
backend, or depend on Zero.

### Step I `.authorized()` note

Step I `AuthorizedQuerySet.authorized` still reads the kernel
accessor. Supported IIb evaluation uses
`implementation_for_path('gh_permissions.backends.GhAuthorizationBackend').configured_backend(...).registry`
(`has_permission` / `filter_authorized` / `permissions_for`).
`Repository.objects` remains `AuthorizedManager()`. A real Step-III
pair is required before core Step III merges; that cut retargets
`.authorized()` at owner `configured_handles()`.

## Migration identity (unchanged)

Loader key remains `('gh_permissions', '0001_initial')` because
`GhPermissionsConfig.label = 'gh_permissions'`. No `MIGRATION_MODULES`.
No `0002`. Models, table names, `ContentType` natural keys
(`gh_permissions | repository` and siblings), and representative rows
do not change.

Already-applied G1 DBs keep matching `django_migrations` rows.
`python -m django migrate --plan` has no GH operations on an
already-current database. `makemigrations gh_permissions --check` is
quiet.

## IIb migration-bot checklist

Search application code and settings for:

```text
INSTALLED_APPS.*trusts
from trusts.apps import kernel_config
kernel_config(
AUTHENTICATION_BACKENDS.*trusts.backends.TrustModelBackend
from trusts.core_backends
import trusts.zero
django-trusts-zero
```

Then:

- [ ] Set `INSTALLED_APPS` to `'gh_permissions'` (plus Django contrib). Remove `'trusts'`.
- [ ] Keep `AUTHENTICATION_BACKENDS = ('gh_permissions.backends.GhAuthorizationBackend',)`.
- [ ] Replace `kernel_config()` donations with `implementation_for_path('gh_permissions.backends.GhAuthorizationBackend', apps_registry=self.apps)` or `gh_config()`.
- [ ] Confirm the canonical backend path is exactly `gh_permissions.backends.GhAuthorizationBackend`.
- [ ] Confirm startup: canonical settings succeed; missing canonical path raises `ImproperlyConfigured` (no silent return).
- [ ] Confirm no installed core `AppConfig` and exactly one implementation owner.
- [ ] `python -m django migrate --plan` — no GH operations on an already-current database.
- [ ] `python -m django makemigrations gh_permissions --check` — quiet.
- [ ] Confirm `ContentType` natural keys `gh_permissions | repository` (and siblings) and representative row identities are unchanged.
- [ ] Confirm model labels, migration key `gh_permissions.0001_initial`, and table names are unchanged.
- [ ] Confirm object/list/enumeration authorization, independent-root OR, fail-closed cases, and fixed query counts.
- [ ] Confirm `pip` metadata requires `django-trusts>=1.0.0.dev2,<2`.
- [ ] Leave package version at `0.1.0.dev0`.
- [ ] Do not implement core Step III/dev3, the `dev4` tombstone removal, Zero changes, examples, or Windows in this PR.

## Out of scope (IIb)

- Core Step III / kernel accessor tombstone / `1.0.0.dev4` deletion
- Zero IIa follow-up
- Windows #17, examples, docs restructuring beyond this IIb record

---

# Prior: G1 reconstitution (C2 + #98, `0.1.0.dev0`)

The remainder records the earlier G1 reconstitution. IIb supersedes its
`INSTALLED_APPS` / `kernel_config()` contract. Persisted identity rows
below remain true.

# Migration record (0.1.0.dev0 G1 reconstitution)

This record covers public APIs and methods introduced by reconstituting
the GH consumer on django-trusts C2 + #98
(`595e2f9f0cc97f8744c1178f3e384dba5787773c`, reviewed head
`5df5eab643d1da39c2ff86bc838b409377590da5`). The previous default-branch
HEAD `dba78890451bca37f475e5cfe429302c2cd814a9` was README-only. Those
surfaces were unavailable; they are not exempt from this record.

G1 is a **complete** consumer on that kernel: both independent roots
are registered at startup. Direct is the three-FK atom. Team is the
accepted django-trusts#54 r7 spelling (`All` / `permission_in` /
`Equal` plus `user=t.team.members`). Independent-root OR is proven.
Do not add a consumer-local Q, lookup-string, tuple, or callable
dialect.

## Changes

### 1. GH domain models and `gh_permissions.0001_initial`

| | |
| --- | --- |
| Previous | README-only / unavailable. No models, no migrations. |
| New | Concrete models `Account`, `Organization`, `Team`, `Repository`, `Operation`, `PermissionBundle`, `AccountRepoGrant`, `TeamRepoGrant` under app label `gh_permissions`, with migration `gh_permissions.0001_initial`. |
| Replacement | Import from `gh_permissions.models`. Ordinary Django ORM for writes (`AccountRepoGrant.objects.create`, `Team.members.add`, …). |
| Affected | New hosts of this package. |
| Authorization | Models alone grant nothing. Membership or attachment without a complete registered relation fails closed. |

Migration-bot checklist:

- [ ] Add `'gh_permissions'` in `INSTALLED_APPS` (**IIb:** do not add `'trusts'`; kernel is a Python dependency only). Do not add `'trusts.zero'`.
- [ ] Run `migrate`. Expect `gh_permissions.0001_initial` only for this package; no `trusts.0001_initial`.
- [ ] Do not inherit `Content` / `Junction` or add `.trusts` / `.trustees` / `.contexts` / `.roles` / `.groups` relations.
- [ ] Treat `organization.teams` / `organization.repositories` as containment, not grants.

### 2. `register_direct` / `register_team`

| | |
| --- | --- |
| Previous | README-only / unavailable. Abandoned proof used `Context.register_identity` + `Trustee.register` (not reconstituted). The parked missing-core stop declared `register_team` but did not invoke it at startup. |
| New | `gh_permissions.policy.register_direct(registry)` registers `Ref(AccountRepoGrant)` (`content=repository`, `user=account`, `permission=operation`). `register_team(registry)` is the accepted team spelling (`user=t.team.members`, `condition=All(permission_in(...), Equal(...))`). `GhPermissionsConfig.ready()` calls **both**, in that order, on the owner store. There is no aggregate `register_gh_policy()`. |
| Replacement | Contribute with `register_direct(handle.registry)` and `register_team(handle.registry)` from `AppConfig.ready()` via `implementation_for_path(...).configured_backend()`. Do not sequence them as one helper that mutates then catches. |
| Affected | Hosts wiring GH policy. Isolated tests may pass a standalone `TrustsRegistry()`. |
| Authorization | Direct grants are SQL `EXISTS` through `AccountRepoGrant`. Team grants are SQL `EXISTS` through `TeamRepoGrant` with membership, bundle ceiling, and organization alignment AND-correlated on that row. Independent roots OR. |

Migration-bot checklist:

- [ ] Do not import `trusts.context` / `trusts.trustee` / alignment-path tuples from the abandoned proof.
- [ ] Pin django-trusts at Step I merge `39f1f9611e214193aec4e97526cf9b54ee689967` (`>=1.0.0.dev2,<2`).
- [ ] Call both `register_direct` and `register_team` from `ready()`. Do not add `register_gh_policy()`.
- [ ] Import `All`, `Equal`, and `permission_in` from `trusts.core` only. Do not add a consumer-local condition dialect.

### 3. `GhAuthorizationBackend`

| | |
| --- | --- |
| Previous | README-only / unavailable. Abandoned proof listed `trusts.backends.ObjectAuthorizationBackend`. |
| New | `gh_permissions.backends.GhAuthorizationBackend` is `TrustModelBackendMixin` + `BaseBackend`. Mixin default compiler is `PlanQueryCompiler`. It is **not** `TrustModelBackend` and does not use Django auth Permission strings. |
| Replacement | `AUTHENTICATION_BACKENDS = ('gh_permissions.backends.GhAuthorizationBackend',)`. |
| Affected | Django populate / owner `configured_backend()` path for the live `TrustsRegistry`. |
| Authorization | Registry projections use this handle. `has_perm` string codec is not the GH surface. |

Migration-bot checklist:

- [ ] Do not list `'trusts.backends.TrustModelBackend'`.
- [ ] Do not copy a query compiler, alignment compiler, or grant-Q helper into this package.

### 4. `Repository.objects.authorized(account, operation)`

| | |
| --- | --- |
| Previous | README-only / unavailable. Abandoned proof accepted operation **strings** (`'read'`) via `operation_lookup='code'`. |
| New | `Repository.objects = AuthorizedManager()`. Instance-only `.authorized(account, operation_instance)`. Strings raise `TrustsConfigurationError` with zero SQL. The same registration drives `registry.has_permission`, `registry.filter_authorized`, `registry.permissions_for`, and (after Step III) the manager. |
| Replacement | Resolve `Operation` yourself (`Operation.objects.get(code=...)`) then pass the instance. On Step I, evaluate through the owner registry (see IIb note above). |
| Affected | Object checks, authorized listings, enumeration. |
| Authorization | Direct and team roots OR in SQL. Object, queryset, and enumeration agree. |

Migration-bot checklist:

- [ ] Replace string operations with `Operation` instances.
- [ ] Paginate only after the authorized filter.
- [ ] Do not attach `.permitted` or `.get_permission` to `Repository` / `Operation`.

### 5. Independent-root OR (direct + team)

| | |
| --- | --- |
| Previous | Issue #3 / preserved proof required multiple complete relation roots to OR-compose (direct + team). The parked missing-core stop registered only the direct root; independent-root OR was unproven. |
| New | Startup registers both roots. A principal who holds a complete team path and a complete direct path sees both resources in one authorized queryset. Removing any required edge of one root (membership, grant row, bundle operation, organization alignment, or direct grant row) removes only that branch. |
| Replacement | Keep both `register_direct` and `register_team` as separate functions. Treat `test_multiple_direct_grant_rows_combine` as same-root grant-row OR, distinct from `test_direct_and_team_roots_or_compose`. |
| Affected | Acceptance evidence and hosts that list repositories across direct and team grants. |
| Authorization | Team membership, bundle ceiling, and cross-org alignment are enforced at read time on the team root. Direct three-FK grants remain unconstrained by those predicates. |

Migration-bot checklist:

- [ ] Do not treat multiple `AccountRepoGrant` rows as independent-root OR evidence.
- [ ] Do not ship a consumer-local extra dialect.
- [ ] Expect one SQL `EXISTS` plan that OR-composes both roots; paginate only after filtering.

## No change to these project-wide rules

- Zero is not a dependency and must not be imported (`trusts.models` stays inert).
- Copied framework authorization glue must remain zero.
- Windows #17, examples, broad docs restructuring, and Zero/admin follow-up stay parked.
