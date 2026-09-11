# Missing-core report (G1 / django-trusts-gh-permissions#3)

Exact kernel: django-trusts C2 merge
`db5a41ed66478b79e10b0a066c8c0bca8fbe7882`.

## Stop

The accepted GH **direct** mapping is expressible on public C2. The
accepted GH **team** mapping is not. G1 stops here: one failing minimal
test (`tests.test_missing_core.MissingCoreTeamMappingTest`) and this
report. This repository does not add a consumer-local Q, lookup-string,
tuple, callable-queryset, or Python-predicate dialect, and it does not
silently widen core.

## Accepted team mapping (django-trusts#54 r7, frozen through r9)

```python
from trusts.core import All, Equal, Ref, permission_in

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

That spelling is `gh_permissions.policy.register_team`. Startup
(`GhPermissionsConfig.ready`) registers only the direct root so Django
populate still succeeds.

## What public C2 actually ships

`TrustsRegistry.register(content, user, permission, condition=None, along=None)`:

| Required team surface | Exact C2 `trusts.core` |
|---|---|
| `All` | **absent** |
| `Equal` | **absent** |
| `permission_in` | **absent** |
| user path = forward single then one membership hop (M2M / reverse O2M) terminating on the requester | **rejected** (`many-to-many and other multi-valued traversals are not supported`) |
| `condition=` typed predicate AST | **rejected** (`condition is not supported; omit it or pass None`) |

User and permission refs remain one direct single-valued hop. `Along` is
bounded reachability, not membership/ceiling/alignment.

## What this consumer still proves on C2

- GH models + migrations for Account, Organization, Team, Repository,
  Operation, PermissionBundle, AccountRepoGrant, TeamRepoGrant.
- One independent direct registration (`Ref(AccountRepoGrant)`).
- `Repository.objects = AuthorizedManager()` with instance-only
  `.authorized(account, operation_instance)`.
- Object / queryset / manager / enumeration agreement on the direct
  root; SQL `EXISTS`; fixed query counts; fail-closed malformed paths
  with zero SQL.
- GH-only populate `trusts` + `gh_permissions`: kernel label
  `trusts_core`, no `trusts` schema/migrations, `trusts.zero` absent,
  inert `trusts.models`.

## Core work this consumer cannot substitute

1. Export and `_meta`-validate `permission_in(*refs)`, `Equal(left, right)`,
   and `All(*predicates)` at `register()` with zero SQL.
2. Widen the user-path grammar to one final membership hop after zero or
   more forward singles.
3. Compile those predicates as an AND overlay on the same permission-bearing
   row (membership, bundle/global-ceiling membership, team/repository
   organization alignment) for object, queryset, and enumeration
   projections.

Until that lands on public C2, reconstituting team grants here would be
an escape dialect.
