"""GH policy registrations on public C2 ``TrustsRegistry.register``.

Two independent roots OR-compose. Direct is the C2 atom (three FKs).
Team is the accepted django-trusts#54 r7 mapping; it is not invoked at
startup because exact C2 ``db5a41ed`` cannot express it. See
``MISSING_CORE.md``.
"""

from trusts.core import Ref, TrustsRegistry

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

    Imports ``All`` / ``Equal`` / ``permission_in`` from ``trusts.core``.
    Exact C2 does not export those names and rejects the membership hop
    and non-None ``condition``. This function is the compact registration
    G1 must supply; it is not a consumer-local dialect.
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


def register_gh_policy(registry=None):
    """Contribute both roots when the live kernel can accept them.

    Direct always registers. Team is attempted so a complete C2 surface
    needs no consumer change; current C2 raises, which G1 must not catch
    and rewrite as a local dialect.
    """
    if registry is None:
        from trusts.apps import kernel_config
        registry = kernel_config().configured_backend().registry
    if not isinstance(registry, TrustsRegistry):
        raise TypeError('registry must be a TrustsRegistry, not %r' % (registry,))
    register_direct(registry)
    register_team(registry)
    return registry
