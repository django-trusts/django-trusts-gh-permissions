"""GH policy registrations on public ``BackendHandle.register_relationship``.

Direct is the three-FK user/repository/operation row. Team is the
accepted mapping: terminal membership hop, team operation ceiling, and
organization alignment. ``GhPermissionsConfig.ready`` contributes both
independent roots as separate calls, not one aggregate helper.
Helpers require a ``BackendHandle``; a bare registry is ``TypeError``.
"""

from trusts.core import All, BackendHandle, Equal, permission_in

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
    return handle.register_relationship(
        UserRepositoryPermission,
        user='user',
        permission='operation',
        content='repository',
    )


def register_team(handle):
    """Register the accepted team mapping (membership, ceiling, alignment)."""
    handle = _require_handle(handle)
    return handle.register_relationship(
        TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=All(
            permission_in('team__allowed_operations'),
            Equal('team__organization', 'repository__organization'),
        ),
    )
