"""GH policy registrations on public C2 ``TrustsRegistry.register``.

Direct is the C2 atom (three FKs). Team is the accepted django-trusts#54
r7 mapping: terminal membership hop, bundle ceiling, and organization
alignment. ``GhPermissionsConfig.ready`` contributes both independent
roots as separate calls, not one aggregate helper.
"""

from trusts.core import All, Equal, Ref, permission_in

from gh_permissions.models import AccountRepoGrant, TeamRepoGrant


def register_direct(registry):
    """Register the direct-account permission-bearing relation."""
    d = Ref(AccountRepoGrant)
    return registry.register(
        content=d.repository,
        user=d.account,
        permission=d.operation,
    )


def register_team(registry):
    """Register the accepted team mapping (membership, ceiling, alignment)."""
    t = Ref(TeamRepoGrant)
    return registry.register(
        content=t.repository,
        user=t.team.members,
        permission=t.operation,
        condition=All(
            permission_in(t.team.permission_bundles.operations),
            Equal(t.team.organization, t.repository.organization),
        ),
    )
