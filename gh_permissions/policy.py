"""GH policy registrations on public ``BackendHandle.register``.

Collaborators are one user/repository row. ``permission`` is the
terminal many-to-many ``permissions``. Team keeps the accepted mapping:
terminal membership step, operation ceiling, and organization alignment.
An owner is one ``OrganizationOwnership`` row. That row reads
``organization__owner_group__permissions`` for the organization and
for ``organization__repositories``, with no condition. A permission in
that group grants only when its ``content_type`` is the object's own
content identity: ``manage_organization`` on the organization, and the
repository codenames on that organization's repositories. The crossed
pairs are denials. A user with no ownership row is not an owner. Team
does not carry this bundle.
``GhPermissionsConfig.ready`` contributes the four helpers as separate
calls. Helpers require a ``BackendHandle``; a bare registry is
``TypeError``. ``permission=`` does not feed ``get_group_permissions``.
"""

from trusts.core import BackendHandle

from gh_permissions.models import (
    AllPersonalRepositoriesDelegation,
    OrganizationOwnership,
    PersonalRepositoryDelegation,
    RepositoryCollaborator,
    RepositoryDelegation,
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


def register_delegation(handle):
    """Register one-level repository delegation through an exact sponsor.

    Selection, organization approval, repository alignment, and the
    delegation ceiling are the left side. Core correlates that row with
    the sponsor's live ordinary GH grants on the right side.
    """
    handle = _require_handle(handle)
    return handle.register(
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


def register_personal_delegation(handle):
    """Register personal-repository delegation without company approval."""
    handle = _require_handle(handle)
    return handle.register(
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


def register_all_personal_repositories_delegation(handle):
    """Register delegation over all of one sponsor's personal repositories."""
    handle = _require_handle(handle)
    return handle.register(
        trust=AllPersonalRepositoriesDelegation,
        delegate='delegate',
        sponsor='sponsor',
        content='sponsor__personal_organization__repositories',
        condition=lambda delegation, permission: (
            delegation.allowed_permissions.contains(permission)
        ),
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


def register_organization_owner(handle):
    """Register each ownership row for one organization and its repositories.

    The ownership row is the grant. No condition is applied.
    """
    handle = _require_handle(handle)
    handle.register(
        trust=OrganizationOwnership,
        user='user',
        permission='organization__owner_group__permissions',
        content='organization',
    )
    return handle.register(
        trust=OrganizationOwnership,
        user='user',
        permission='organization__owner_group__permissions',
        content='organization__repositories',
    )
