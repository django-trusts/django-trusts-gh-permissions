"""GH policy registrations on public C2 ``TrustsRegistry.register``.

Direct is the C2 atom (three FKs) and is the only root contributed at
startup. Team is the accepted django-trusts#54 r7 mapping; it is not
invoked at startup because exact C2 ``db5a41ed`` cannot express it.
Independent-root OR is unproven until that team root is expressible.
See ``MISSING_CORE.md``.
"""

from trusts.core import Ref

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
    """Register the accepted team mapping (membership, ceiling, alignment).

    Imports ``All`` / ``Equal`` / ``permission_in`` from ``trusts.core``
    before any ``registry.register`` call. Exact C2 does not export those
    names and rejects the membership hop and non-None ``condition``.
    This function is the compact registration G1 must supply; it is not
    a consumer-local dialect. Do not sequence it after ``register_direct``
    as one aggregate helper.
    """
    from trusts.core import All, Equal, permission_in

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
