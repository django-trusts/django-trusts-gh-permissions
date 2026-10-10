# Repository delegation example

This example tests one level of delegated repository authority. More than one
level is out of scope.

The example has three independent relationship tables for the same `Repository`
content model:

- `PersonalRepositoryDelegation` records a delegate, sponsor, repository, and
  permission ceiling. The repository must belong to the sponsor's personal
  organization. It needs no separate organization approval.
- `AllPersonalRepositoriesDelegation` records a delegate, sponsor, and
  permission ceiling, but no repository. Its content path reaches every
  repository in the sponsor's personal organization. It also needs no
  separate approval.
- `RepositoryDelegation` records the delegate, the exact
  `OrganizationOwnership` that selected the bridge, the repository, the
  permission ceiling, and the conventional organization's approval.

None of these tables is an ordinary permission grant.

For a delegate `D`, repository `R`, and permission `P`, the effective rule is:

```text
ordinary(D, R, P)
OR
(
    personal_delegation(D, S1, R)
    AND R belongs to S1's personal organization
    AND P is in the personal delegation ceiling
    AND ordinary(S1, R, P)
)
OR
(
    all_personal_repositories_delegation(D, S2)
    AND R belongs to S2's personal organization
    AND P is in the all-personal delegation ceiling
    AND ordinary(S2, R, P)
)
OR
(
    selected_sponsor_ownership(D, S3, R)
    AND approved_by_repository_organization(D, S3, R)
    AND P is in the delegation ceiling
    AND ordinary(S3, R, P)
)
```

Each `ordinary(S, R, P)` is the existing union of direct collaborator, team, and
organization-owner grants. It does not include delegated grants. The same
delegation can therefore follow any live ordinary sponsor path, but delegated
authority cannot sponsor another delegation.

The three completed relationship paths are ORed. Their pieces are not flattened
into `(left1 OR left2 OR left3) AND (sponsor1 OR sponsor2 OR sponsor3)`. Each
relationship's left-side requirements stay correlated with the sponsor from
that exact row, so the organization path cannot borrow the personal path's
lack of an approval requirement.

The selected ownership remains an independent left-side requirement. Removing
that ownership deletes the bridge and revokes the delegate even when the
sponsor still has a direct or team grant on the repository.

`approved_organization` is the authoritative approval fact.
`approved_by` is optional audit data, not an authorization predicate, and may
become null if that user is deleted. A trusted organization-approval write
path must be the only code allowed to set or clear the approval fact. This
bounded authorization example does not implement that workflow.

If a repository is moved to another organization by a raw write, both the
sponsor-ownership alignment and approval alignment fail closed. The supported
repository services already refuse organization moves.

The registrations are:

```python
handle.register(
    trust=PersonalRepositoryDelegation,
    delegate='delegate',
    sponsor='sponsor',
    content='repository',
    condition=lambda delegation, permission: (
        delegation.allowed_permissions.contains(permission)
        & (
            delegation.repository.organization.personal_user
            == delegation.sponsor
        )
    ),
)

handle.register(
    trust=AllPersonalRepositoriesDelegation,
    delegate='delegate',
    sponsor='sponsor',
    content='sponsor__personal_organization__repositories',
    condition=lambda delegation, permission: (
        delegation.allowed_permissions.contains(permission)
    ),
)

handle.register(
    trust=RepositoryDelegation,
    delegate='delegate',
    sponsor='sponsor_ownership__user',
    content='repository',
    condition=lambda delegation, permission: (
        delegation.allowed_permissions.contains(permission)
        & (
            delegation.sponsor_ownership.organization
            == delegation.repository.organization
        )
        & (
            delegation.approved_organization
            == delegation.repository.organization
        )
    ),
)
```

The committed `trusts-policy.lock.yaml` is the audit artifact. For every
inquiry it shows three sibling delegation `EXISTS` branches. Each branch contains
its own selection, alignment, and permission-ceiling predicates and its own
inner ordinary-only union of the three GH grant paths. Only the conventional
organization branch contains the independent approval predicate.
