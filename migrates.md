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

- [ ] Add `'gh_permissions'` next to `'trusts'` in `INSTALLED_APPS` (kernel label is `trusts_core`; do not add `'trusts.zero'`).
- [ ] Run `migrate`. Expect `gh_permissions.0001_initial` only for this package; no `trusts.0001_initial`.
- [ ] Do not inherit `Content` / `Junction` or add `.trusts` / `.trustees` / `.contexts` / `.roles` / `.groups` relations.
- [ ] Treat `organization.teams` / `organization.repositories` as containment, not grants.

### 2. `register_direct` / `register_team`

| | |
| --- | --- |
| Previous | README-only / unavailable. Abandoned proof used `Context.register_identity` + `Trustee.register` (not reconstituted). The parked missing-core stop declared `register_team` but did not invoke it at startup. |
| New | `gh_permissions.policy.register_direct(registry)` registers `Ref(AccountRepoGrant)` (`content=repository`, `user=account`, `permission=operation`). `register_team(registry)` is the accepted team spelling (`user=t.team.members`, `condition=All(permission_in(...), Equal(...))`). `GhPermissionsConfig.ready()` calls **both**, in that order, on the kernel store. There is no aggregate `register_gh_policy()`. |
| Replacement | Contribute with `register_direct(handle.registry)` and `register_team(handle.registry)` from `AppConfig.ready()` via `kernel_config().configured_backend()`. Do not sequence them as one helper that mutates then catches. |
| Affected | Hosts wiring GH policy. Isolated tests may pass a standalone `TrustsRegistry()`. |
| Authorization | Direct grants are SQL `EXISTS` through `AccountRepoGrant`. Team grants are SQL `EXISTS` through `TeamRepoGrant` with membership, bundle ceiling, and organization alignment AND-correlated on that row. Independent roots OR. |

Migration-bot checklist:

- [ ] Do not import `trusts.context` / `trusts.trustee` / alignment-path tuples from the abandoned proof.
- [ ] Pin django-trusts at #98 merge `595e2f9f0cc97f8744c1178f3e384dba5787773c` (or a later published revision that includes it).
- [ ] Call both `register_direct` and `register_team` from `ready()`. Do not add `register_gh_policy()`.
- [ ] Import `All`, `Equal`, and `permission_in` from `trusts.core` only. Do not add a consumer-local condition dialect.

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
| Authorization | Direct and team roots OR in SQL. Object, queryset, manager, and enumeration agree. |

Migration-bot checklist:

- [ ] Replace string operations with `Operation` instances.
- [ ] Paginate only after `.authorized(...)`.
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
