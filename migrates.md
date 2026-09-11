# Migration record (0.1.0.dev0 G1 reconstitution)

This record covers public APIs and methods introduced by reconstituting
the GH consumer on django-trusts C2
(`db5a41ed66478b79e10b0a066c8c0bca8fbe7882`). The previous default-branch
HEAD `dba78890451bca37f475e5cfe429302c2cd814a9` was README-only. Those
surfaces were unavailable; they are not exempt from this record.

G1 is a **partial** consumer: the direct-account root is registered at
startup; the accepted team root is declared as `register_team` and is
not invoked at startup. Independent-root OR is unproven until core
gains the typed predicates and membership hop. Do not merge this as a
complete G1 until that core slice lands and this record is updated.

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

- [ ] Add `'gh_permissions'` next to `'trusts'` in `INSTALLED_APPS` (kernel label is `trusts_core`; do not add `'trusts.zero'`).
- [ ] Run `migrate`. Expect `gh_permissions.0001_initial` only for this package; no `trusts.0001_initial`.
- [ ] Do not inherit `Content` / `Junction` or add `.trusts` / `.trustees` / `.contexts` / `.roles` / `.groups` relations.
- [ ] Treat `organization.teams` / `organization.repositories` as containment, not grants.

### 2. `register_direct` / `register_team`

| | |
| --- | --- |
| Previous | README-only / unavailable. Abandoned proof used `Context.register_identity` + `Trustee.register` (not reconstituted). |
| New | `gh_permissions.policy.register_direct(registry)` registers `Ref(AccountRepoGrant)` (`content=repository`, `user=account`, `permission=operation`). `register_team(registry)` is the accepted team spelling (`user=t.team.members`, `condition=All(permission_in(...), Equal(...))`). `GhPermissionsConfig.ready()` calls **only** `register_direct`. There is no aggregate `register_gh_policy()`. |
| Replacement | Contribute with `register_direct(handle.registry)` from `AppConfig.ready()` via `kernel_config().configured_backend()`. Do not sequence `register_direct` then `register_team` as one helper: on current C2 `register_team` raises before `register()`, but an aggregate that registered direct first would leave a partial policy if that exception were caught. |
| Affected | Hosts wiring GH policy. Isolated tests may pass a standalone `TrustsRegistry()`. |
| Authorization | Direct grants are SQL `EXISTS` through `AccountRepoGrant`. Team grants are **not** live. Independent-root OR is unproven. |

Migration-bot checklist:

- [ ] Do not import `trusts.context` / `trusts.trustee` / alignment-path tuples from the abandoned proof.
- [ ] Do not call `register_team` from `ready()` until public C2 accepts that mapping (see `MISSING_CORE.md`).
- [ ] Do not add `register_gh_policy()` that mutates then fails.
- [ ] On current C2, `register_team` fails before `registry.register` (`ImportError` for `All` / `Equal` / `permission_in`); stored records stay unchanged.

### 3. `GhAuthorizationBackend`

| | |
| --- | --- |
| Previous | README-only / unavailable. Abandoned proof listed `trusts.backends.ObjectAuthorizationBackend`. |
| New | `gh_permissions.backends.GhAuthorizationBackend` is `TrustModelBackendMixin` + `BaseBackend`. Mixin default compiler is `PlanQueryCompiler`. It is **not** `TrustModelBackend` and does not use Django auth Permission strings. |
| Replacement | `AUTHENTICATION_BACKENDS = ('gh_permissions.backends.GhAuthorizationBackend',)`. |
| Affected | Django populate / `configured_backend()` path for the live `TrustsRegistry`. |
| Authorization | `.authorized` / registry projections use this handle. `has_perm` string codec is not the GH surface. |

Migration-bot checklist:

- [ ] Do not list `'trusts.backends.TrustModelBackend'`.
- [ ] Do not copy a query compiler, alignment compiler, or grant-Q helper into this package.

### 4. `Repository.objects.authorized(account, operation)`

| | |
| --- | --- |
| Previous | README-only / unavailable. Abandoned proof accepted operation **strings** (`'read'`) via `operation_lookup='code'`. |
| New | `Repository.objects = AuthorizedManager()`. Instance-only `.authorized(account, operation_instance)`. Strings raise `TrustsConfigurationError` with zero SQL. The same registration drives `registry.has_permission`, `registry.filter_authorized`, `registry.permissions_for`, and the manager. |
| Replacement | Resolve `Operation` yourself (`Operation.objects.get(code=...)`) then pass the instance. |
| Affected | Object checks, authorized listings, enumeration. |
| Authorization | Direct-root only on this stop PR. Multiple `AccountRepoGrant` **rows** OR in SQL; that is not independent-root OR. |

Migration-bot checklist:

- [ ] Replace string operations with `Operation` instances.
- [ ] Paginate only after `.authorized(...)`.
- [ ] Do not attach `.permitted` or `.get_permission` to `Repository` / `Operation`.
- [ ] Do not claim object/list agreement across direct **and** team roots until `register_team` is live.

### 5. Independent-root OR is unproven on this stop

| | |
| --- | --- |
| Previous | Issue #3 / preserved proof required multiple complete relation roots to OR-compose (direct + team). |
| New | Only the direct root is registered. Tests that create two `AccountRepoGrant` rows prove multiple **grant rows** under one registration, not OR-composition of independent relation roots. Independent-root OR remains unproven until the team root is expressible. |
| Replacement | Keep both `register_direct` and `register_team` as separate functions. Re-prove OR after core ships `All` / `Equal` / `permission_in` and the membership hop. |
| Affected | Acceptance evidence and any host that expected team+direct listing in one queryset. |
| Authorization | Team membership, bundle ceiling, and cross-org alignment are **not** enforced by a live team registration on this PR. |

Migration-bot checklist:

- [ ] Do not treat `test_multiple_direct_grant_rows_combine` as independent-root OR evidence.
- [ ] Do not ship a consumer-local extra dialect to fake the team root.
- [ ] After the core predicate/membership slice, update this section and add a two-root OR test.

## No change to these project-wide rules

- Zero is not a dependency and must not be imported (`trusts.models` stays inert).
- Copied framework authorization glue must remain zero.
- Windows #17, examples, broad docs restructuring, and Zero/admin follow-up stay parked.
