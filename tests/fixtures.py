"""Shared GH fixtures. Not authorization policy."""

from gh_permissions.models import (
    Account,
    AccountRepoGrant,
    Operation,
    Organization,
    PermissionBundle,
    Repository,
    Team,
    TeamRepoGrant,
)


class GhFixtureMixin(object):
    """Two orgs, team + direct paths, and the partial-path counters."""

    def setUp(self):
        super(GhFixtureMixin, self).setUp()
        self.org_a = Organization.objects.create(name='acme')
        self.org_b = Organization.objects.create(name='other')
        self.writers = Team.objects.create(
            organization=self.org_a, name='writers',
        )
        self.outsiders = Team.objects.create(
            organization=self.org_b, name='outsiders',
        )
        self.member = Account.objects.create(name='member')
        self.collaborator = Account.objects.create(name='collaborator')
        self.org_only = Account.objects.create(name='org-only')
        self.stranger = Account.objects.create(name='stranger')
        self.org_a.members.add(self.member, self.org_only)
        self.org_b.members.add(self.stranger)
        self.writers.members.add(self.member)
        self.read = Operation.objects.create(code='read')
        self.write = Operation.objects.create(code='write')
        self.admin = Operation.objects.create(code='admin')
        self.bundle = PermissionBundle.objects.create(
            team=self.writers, name='reader',
        )
        self.bundle.operations.add(self.read)
        self.repo_a = Repository.objects.create(
            organization=self.org_a, title='repo-a',
        )
        self.repo_b = Repository.objects.create(
            organization=self.org_a, title='repo-b',
        )
        self.repo_other = Repository.objects.create(
            organization=self.org_b, title='repo-other',
        )
        TeamRepoGrant.objects.create(
            team=self.writers, repository=self.repo_a, operation=self.read,
        )
        AccountRepoGrant.objects.create(
            account=self.collaborator, repository=self.repo_b,
            operation=self.write,
        )
