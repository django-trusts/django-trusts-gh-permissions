"""GH policy registrations on public ``BackendHandle.register``.

Collaborators are one user/repository row. ``permission`` is the
terminal many-to-many ``permissions``. Team keeps the accepted mapping:
terminal membership step, operation ceiling, and organization alignment.
``GhPermissionsConfig.ready`` contributes those two roots as separate
calls. Helpers require a ``BackendHandle``; a bare registry is
``TypeError``.

``register_organization_owner`` is the owner relationship:
``OrganizationMembership`` filtered with ``is_owner == True``, reading
``organization__owner_group__permissions``, for the organization and
for ``organization__repositories``. The public condition grammar
rejects a boolean comparison, so startup does not call that helper.
An unfiltered membership registration would grant every membership
the owner bundle. ``permission=`` does not feed ``get_group_permissions``.
"""

from trusts.core import BackendHandle

from gh_permissions.models import (
    OrganizationMembership,
    RepositoryCollaborator,
    TeamRepositoryPermission,
)


def _require_handle(handle):
    if not isinstance(handle, BackendHandle):
        raise TypeError(
            'GH relation helpers require a BackendHandle, not %r.'
            % (type(handle).__name__,)
        )
    return handle


def register_collaborator(handle):
    """Register direct repository collaboration."""
    handle = _require_handle(handle)
    return handle.register(
        trust=RepositoryCollaborator,
        user='user',
        permission='permissions',
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


def _owner_condition(membership):
    return membership.is_owner == True  # noqa: E712


def register_organization_owner(handle):
    """Register owner memberships for one organization and its repositories.

    ``condition`` is ``is_owner == True``. Core rejects that spelling.
    This helper raises ``TrustsConfigurationError`` and stores nothing.
    Startup does not call it.
    """
    handle = _require_handle(handle)
    handle.register(
        trust=OrganizationMembership,
        user='user',
        permission='organization__owner_group__permissions',
        content='organization',
        condition=_owner_condition,
    )
    return handle.register(
        trust=OrganizationMembership,
        user='user',
        permission='organization__owner_group__permissions',
        content='organization__repositories',
        condition=_owner_condition,
    )
