# Repository delegation example

This example tests one level of delegated repository authority. More than one
level is out of scope.

`RepositoryDelegation` is a sponsor-selected bridge. It records the delegate,
the exact `OrganizationOwnership` that selected the bridge, the repository,
the allowed permission ceiling, and the organization's approval. It is not an
ordinary permission grant.

For a delegate `D`, repository `R`, and permission `P`, the effective rule is:

```text
ordinary(D, R, P)
OR
(
    selected_sponsor_ownership(D, S, R)
    AND approved_by_repository_organization(D, S, R)
    AND P is in the delegation ceiling
    AND ordinary(S, R, P)
)
```

`ordinary(S, R, P)` is the existing union of direct collaborator, team, and
organization-owner grants. It does not include delegated grants. The same
delegation can therefore follow any live ordinary sponsor path, but delegated
authority cannot sponsor another delegation.

The selected ownership remains an independent left-side requirement. Removing
that ownership deletes the bridge and revokes the delegate even when the
sponsor still has a direct or team grant on the repository.

The registration is:

```python
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
inquiry it shows one delegation `EXISTS`, the selection, approval, alignment,
and permission-ceiling predicates, followed by an inner ordinary-only union of
the three GH grant paths.
