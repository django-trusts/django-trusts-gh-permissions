"""GH policy registrations on this app's ``Recorder``.

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
Issue triage assignments use bounded ``along=`` reachability: one grant at an
issue applies to that issue and its sub-issues through at most eight parent
edges. This relative query bound is separate from the modeled service's
absolute eight-level validity rule.
``GhPermissionsConfig.ready`` contributes each helper as a separate call.
Helpers require a ``Recorder``; anything else is ``TypeError``.
``permission=`` does not feed ``get_group_permissions``.
"""

from trusts.apps import Recorder

from gh_permissions.models import (
    AllPersonalRepositoriesDelegation,
    IssuePermissions,
    OrganizationOwnership,
    PersonalRepositoryDelegation,
    RepositoryCollaborator,
    RepositoryDelegation,
    TeamRepositoryPermission,
)


def _require_recorder(recorder):
    if not isinstance(recorder, Recorder):
        raise TypeError(
            'GH relation helpers require a Recorder, not %r.'
            % (type(recorder).__name__,)
        )
    return recorder


def register_collaborator(recorder):
    """Register direct repository collaboration."""
    recorder = _require_recorder(recorder)
    return recorder.register(
        trust=RepositoryCollaborator,
        user='user',
        permission='permissions',
        content='repository',
    )


def register_issue_permissions(recorder):
    """Register a bounded permission bundle over one issue subtree."""
    recorder = _require_recorder(recorder)
    return recorder.register(
        trust=IssuePermissions,
        user='user',
        permission='permissions',
        content='issue',
        along=('issue__parent', 8),
    )


def register_delegation(recorder):
    """Register one-level repository delegation through an exact sponsor.

    Selection, organization approval, repository alignment, and the
    delegation ceiling are the left side. Core correlates that row with
    the sponsor's live ordinary GH grants on the right side.
    """
    recorder = _require_recorder(recorder)
    return recorder.register(
        trust=RepositoryDelegation,
        delegate='delegate',
        sponsor='sponsor_ownership__user',
        content='repository',
        condition=lambda delegation, permission: (
            delegation.permissions.contains(permission)
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


def register_personal_delegation(recorder):
    """Register personal-repository delegation without company approval."""
    recorder = _require_recorder(recorder)
    return recorder.register(
        trust=PersonalRepositoryDelegation,
        delegate='delegate',
        sponsor='sponsor',
        content='repository',
        condition=lambda delegation, permission: (
            delegation.permissions.contains(permission)
            & (
                delegation.repository.organization.personal_user
                == delegation.sponsor
            )
        ),
    )


def register_all_personal_repositories_delegation(recorder):
    """Register delegation over all of one sponsor's personal repositories."""
    recorder = _require_recorder(recorder)
    return recorder.register(
        trust=AllPersonalRepositoriesDelegation,
        delegate='delegate',
        sponsor='sponsor',
        content='sponsor__personal_organization__repositories',
        condition=lambda delegation, permission: (
            delegation.permissions.contains(permission)
        ),
    )


def register_team(recorder):
    """Register the accepted team mapping (membership, ceiling, alignment)."""
    recorder = _require_recorder(recorder)
    return recorder.register(
        trust=TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=lambda t: (
            t.team.allowed_operations.contains(t.operation)
            & (t.team.organization == t.repository.organization)
        ),
    )


def register_organization_owner(recorder):
    """Register each ownership row for one organization and its repositories.

    The ownership row is the grant. No condition is applied.
    """
    recorder = _require_recorder(recorder)
    recorder.register(
        trust=OrganizationOwnership,
        user='user',
        permission='organization__owner_group__permissions',
        content='organization',
    )
    return recorder.register(
        trust=OrganizationOwnership,
        user='user',
        permission='organization__owner_group__permissions',
        content='organization__repositories',
    )
