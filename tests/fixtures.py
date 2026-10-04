"""Shared GH fixtures. Not authorization policy."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

from gh_permissions.models import (
    Organization,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import ensure_owner_group


def repository_permission(codename):
    """Saved ``auth.Permission`` for a Repository codename."""
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model='repository',
        codename=codename,
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
        owner_group = ensure_owner_group()
        self.org_a = Organization.objects.create(
            name='acme', owner_group=owner_group,
        )
        self.org_b = Organization.objects.create(
            name='other', owner_group=owner_group,
        )
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
        self.read = repository_permission('read_repository')
        self.write = repository_permission('write_repository')
        self.admin = repository_permission('admin_repository')
        self.writers.allowed_operations.add(self.read)
        self.repo_a = Repository.objects.create(
            organization=self.org_a, name='repo-a',
        )
        self.repo_b = Repository.objects.create(
            organization=self.org_a, name='repo-b',
        )
        self.repo_other = Repository.objects.create(
            organization=self.org_b, name='repo-other',
        )
        TeamRepositoryPermission.objects.create(
            team=self.writers, repository=self.repo_a, operation=self.read,
        )
        collaboration = RepositoryCollaborator.objects.create(
            user=self.collaborator, repository=self.repo_b,
        )
        collaboration.permissions.add(self.write)
