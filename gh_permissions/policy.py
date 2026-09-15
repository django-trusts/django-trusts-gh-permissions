"""GH policy registrations on public ``BackendHandle.register``.

Direct is the three-FK user/repository/operation row. Team is the
accepted mapping: terminal membership hop, team operation ceiling, and
organization alignment. ``GhPermissionsConfig.ready`` contributes both
independent roots as separate calls, not one aggregate helper.
Helpers require a ``BackendHandle``; a bare registry is ``TypeError``.
"""

from trusts.core import BackendHandle

from gh_permissions.models import TeamRepositoryPermission, UserRepositoryPermission


def _require_handle(handle):
    if not isinstance(handle, BackendHandle):
        raise TypeError(
            'GH relation helpers require a BackendHandle, not %r.'
            % (type(handle).__name__,)
        )
    return handle


def register_direct(handle):
    """Register the direct-user permission-bearing relation."""
    handle = _require_handle(handle)
    return handle.register(
        trust=UserRepositoryPermission,
        user='user',
        permission='operation',
        content='repository',
    )


def register_team(handle):
    """Register the accepted team mapping (membership, ceiling, alignment)."""
    handle = _require_handle(handle)
    return handle.register(
        trust=TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=lambda t: (
            t.team.allowed_operations.contains(t.operation)
            & (t.team.organization == t.repository.organization)
        ),
    )
