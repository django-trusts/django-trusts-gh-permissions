"""GH policy registrations.

``models.py`` contributes each helper as a separate call on
``GhAuthorizationBackend().get_registry()``. Collaborators are one
user/repository row. ``permission`` is the terminal many-to-many
``permissions``. Team keeps the accepted mapping: terminal membership
step, operation ceiling, and organization alignment. An owner is one
``OrganizationOwnership`` row. That row reads
``organization__owner_group__permissions`` for the organization and
for ``organization__repositories``, with no condition. A permission in
that group grants only when its ``content_type`` is the object's own
content identity: ``manage_organization`` on the organization, and the
repository codenames on that organization's repositories. The crossed
pairs are denials. A user with no ownership row is not an owner. Team
does not carry this bundle.
Issue triage assignments use bounded ``along=`` reachability: one grant at an
issue applies to that issue and its sub-issues through at most eight parent
edges, subject to ``Issue.reset_inheritance``. This relative query bound is
separate from the modeled service's absolute eight-level validity rule.
``permission=`` does not feed ``get_group_permissions``.
"""

from gh_permissions.models import (
    AllPersonalRepositoriesDelegation,
    IssueCloseGrant,
    IssuePermissions,
    OrganizationOwnership,
    PersonalRepositoryDelegation,
    RepositoryCollaborator,
    RepositoryDelegation,
    TeamRepositoryPermission,
)


def register_collaborator(registry):
    """Register direct repository collaboration."""
    registry.register(
        trust=RepositoryCollaborator,
        user='user',
        permission='permissions',
        content='repository',
        name='collaborator',
        description=(
            'A user holds the permissions on this row for one repository.'
        ),
    )


def register_issue_close_grants(registry):
    """Register the draft issue-closing policy on a quantifier-capable Core."""
    from trusts.functions import All

    registry.register(
        trust=IssueCloseGrant,
        user='user',
        permission='permission',
        content='issue',
        condition=lambda grant: (
            (grant.permission.codename == 'close_issue')
            & All(grant.issue.blocked_by, lambda blocker: blocker.is_closed)
        ),
    )


def register_issue_permissions(registry):
    """Register a bounded permission bundle over one issue subtree."""
    registry.register(
        trust=IssuePermissions,
        user='user',
        permission='permissions',
        content='issue',
        along=('issue__parent', 8, 'reset_inheritance'),
        name='issue_subtree',
        description=(
            'A user holds the permissions on this row for one issue and '
            'its sub-issues, through at most eight parent edges. '
            'reset_inheritance true cuts grants inherited from ancestors; '
            'a grant on that issue still applies and continues. '
            'Null and false inherit.'
        ),
    )


def register_delegation(registry):
    """Register one-level repository delegation through an exact sponsor.

    Selection, organization approval, repository alignment, and the
    delegation ceiling are the left side. Core correlates that row with
    the sponsor's live ordinary GH grants on the right side.
    """
    registry.register(
        trust=RepositoryDelegation,
        delegate='delegate',
        sponsor='sponsor_ownership__user',
        content='repository',
        name='repository_delegation',
        description=(
            'A delegate may use one sponsor\'s grants on a repository '
            'when this row allows the permission, the sponsor ownership '
            'is in that repository\'s organization, and the approved '
            'organization is that same organization.'
        ),
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


def register_personal_delegation(registry):
    """Register personal-repository delegation without company approval."""
    registry.register(
        trust=PersonalRepositoryDelegation,
        delegate='delegate',
        sponsor='sponsor',
        content='repository',
        name='personal_repository_delegation',
        description=(
            'A delegate may use the sponsor\'s grants on one personal '
            'repository when this row allows the permission and that '
            'repository\'s organization belongs to the sponsor.'
        ),
        condition=lambda delegation, permission: (
            delegation.permissions.contains(permission)
            & (
                delegation.repository.organization.personal_user
                == delegation.sponsor
            )
        ),
    )


def register_all_personal_repositories_delegation(registry):
    """Register delegation over all of one sponsor's personal repositories."""
    registry.register(
        trust=AllPersonalRepositoriesDelegation,
        delegate='delegate',
        sponsor='sponsor',
        content='sponsor__personal_organization__repositories',
        name='all_personal_repositories',
        description=(
            'A delegate may use the sponsor\'s grants on every repository '
            'in the sponsor\'s personal organization when this row allows '
            'the permission.'
        ),
        condition=lambda delegation, permission: (
            delegation.permissions.contains(permission)
        ),
    )


def register_team(registry):
    """Register the accepted team mapping (membership, ceiling, alignment)."""
    registry.register(
        trust=TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        name='team_repository',
        description=(
            'A team member may perform an operation on a repository in '
            'the team\'s organization when that operation is in the '
            'team\'s allowed set.'
        ),
        condition=lambda t: (
            t.team.allowed_operations.contains(t.operation)
            & (t.team.organization == t.repository.organization)
        ),
    )


def register_organization_owner(registry):
    """Register each ownership row for one organization and its repositories.

    The ownership row is the grant. No condition is applied.
    """
    registry.register(
        trust=OrganizationOwnership,
        user='user',
        permission='organization__owner_group__permissions',
        content='organization',
        name='organization_owner',
        description=(
            'An ownership row grants the organization owner group\'s '
            'permissions on that organization.'
        ),
    )
    registry.register(
        trust=OrganizationOwnership,
        user='user',
        permission='organization__owner_group__permissions',
        content='organization__repositories',
        name='organization_owner_repositories',
        description=(
            'An ownership row grants the organization owner group\'s '
            'permissions on that organization\'s repositories.'
        ),
    )
