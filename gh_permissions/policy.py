"""Explicit GH policy registrations on django-trusts S1 maps.

Repository is an identity Context. Team and direct adapters OR-compose.
Organization alignment is a grant-row equality, not a Python predicate.
"""

from trusts.context import Context
from trusts.trustee import Trustee

from gh_permissions.models import (
    Account, AccountRepoGrant, Operation, Repository, Team, TeamRepoGrant,
)

TEAM_ADAPTER = 'team'
DIRECT_ADAPTER = 'direct'
TEAM_ALIGNMENT_PATHS = (('team__organization', 'repository__organization'),)
TEAM_CONSTRAINT_PATHS = ('team__permission_bundles__operations',)


def register_gh_policy(context=None, trustee=None):
    """Install identity Context plus team/direct adapters.

    Defaults to the process-wide maps. Isolated tests may pass
    ``ContextRegistry`` / ``TrusteeRegistry`` instances.
    """
    if context is None:
        context = Context
    if trustee is None:
        trustee = Trustee
    context.register_identity(Repository)
    trustee.configure(
        requester_model=Account,
        scope_model=Repository,
        operation_model=Operation,
        operation_lookup='code',
    )
    trustee.register(
        name=TEAM_ADAPTER,
        trustee_model=Team,
        grant_model=TeamRepoGrant,
        trustee_path='team',
        scope_path='repository',
        operation_path='operation',
        membership_path='members',
        constraint_paths=TEAM_CONSTRAINT_PATHS,
        alignment_paths=TEAM_ALIGNMENT_PATHS,
    )
    trustee.register(
        name=DIRECT_ADAPTER,
        trustee_model=Account,
        grant_model=AccountRepoGrant,
        trustee_path='account',
        scope_path='repository',
        operation_path='operation',
        membership_path='',
    )
    return context, trustee
