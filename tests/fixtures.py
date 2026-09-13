"""Shared GH fixtures. Not authorization policy."""

from django.contrib.auth import get_user_model

from gh_permissions.models import (
    Operation,
    Organization,
    Repository,
    Team,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)


def isolated_handle(registry=None):
    """Wrap an unfrozen registry in a ``BackendHandle`` for isolated tests."""
    from trusts.core import BackendHandle, PlanQueryCompiler, TrustsRegistry

    if registry is None:
        registry = TrustsRegistry()
    return BackendHandle(
        path='tests.isolated',
        registry=registry,
        compiler=PlanQueryCompiler(),
    )


class GhFixtureMixin(object):
    """Two orgs, team + direct paths, and the partial-path counters."""

    def setUp(self):
        super(GhFixtureMixin, self).setUp()
        User = get_user_model()
        self.org_a = Organization.objects.create(name='acme')
        self.org_b = Organization.objects.create(name='other')
        self.writers = Team.objects.create(
            organization=self.org_a, name='writers',
        )
        self.outsiders = Team.objects.create(
            organization=self.org_b, name='outsiders',
        )
        self.member = User.objects.create(username='member')
        self.collaborator = User.objects.create(username='collaborator')
        self.unattached = User.objects.create(username='unattached')
        self.stranger = User.objects.create(username='stranger')
        self.writers.members.add(self.member)
        self.read = Operation.objects.create(code='read')
        self.write = Operation.objects.create(code='write')
        self.admin = Operation.objects.create(code='admin')
        self.writers.allowed_operations.add(self.read)
        self.repo_a = Repository.objects.create(
            organization=self.org_a, title='repo-a',
        )
        self.repo_b = Repository.objects.create(
            organization=self.org_a, title='repo-b',
        )
        self.repo_other = Repository.objects.create(
            organization=self.org_b, title='repo-other',
        )
        TeamRepositoryPermission.objects.create(
            team=self.writers, repository=self.repo_a, operation=self.read,
        )
        UserRepositoryPermission.objects.create(
            user=self.collaborator, repository=self.repo_b,
            operation=self.write,
        )
