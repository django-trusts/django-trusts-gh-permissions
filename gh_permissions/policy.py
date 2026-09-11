"""GH registrations on the C2 public atom.

Direct ``AccountRepoGrant`` already matches
``register(content=, user=, permission=)``. The accepted team mapping is
``register_team()``. Live C2 at ``db5a41ed`` cannot express that mapping;
see README missing-core report. ``ready()`` must not call ``register_team``.
"""

from trusts.core import Ref

from gh_permissions.models import AccountRepoGrant, TeamRepoGrant


def register_direct(registry):
    """One independent registration for the direct-account relation."""
    d = Ref(AccountRepoGrant)
    return registry.register(
        content=d.repository,
        user=d.account,
        permission=d.operation,
    )


def register_team(registry):
    """Accepted team mapping (r7 §6). Requires C2 predicates not yet shipped.

    Imports and ``register(condition=...)`` are inside the function so
    GH-only startup stays importable on current C2.
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


def register_gh(registry):
    """Contribute every C2-expressible GH root onto ``registry``."""
    register_direct(registry)
    return registry
