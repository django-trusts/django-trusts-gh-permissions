"""GH policy registrations on public C2 ``TrustsRegistry.register``.

Direct is the three-FK user/repository/operation atom. Team is the
accepted django-trusts#54 r7 mapping: terminal membership hop, the
team-operation ceiling, and organization alignment.
``GhPermissionsConfig.ready`` contributes both independent roots as
separate calls, not one aggregate helper.
"""

from trusts.core import All, Equal, Ref, permission_in

from gh_permissions.models import TeamRepositoryPermission, UserRepositoryPermission


def register_direct(registry):
    """Register the direct-user permission-bearing relation."""
    d = Ref(UserRepositoryPermission)
    return registry.register(
        content=d.repository,
        user=d.user,
        permission=d.operation,
    )


def register_team(registry):
    """Register the accepted team mapping (membership, ceiling, alignment)."""
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
