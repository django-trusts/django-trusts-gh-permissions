"""Shared GH fixtures. Not authorization policy."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

from trusts.apps import Recorder
from trusts.compile.factory import Compiler
from trusts.plan.relations import PlanQueryCompiler

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


def isolated_recorder():
    """Unfrozen recorder. Replay it with ``compile_recorder``."""
    return Recorder()


def isolated_compiler():
    """Unfrozen compiler for one registration shape. Not the live catalog."""
    return Compiler(
        user_model=get_user_model(),
        query_compiler=PlanQueryCompiler(),
        path='tests.isolated.Compiler',
        family='relationship',
    )


def compile_recorder(recorder):
    """Freeze ``recorder``, replay it, and return the built catalog."""
    if not recorder.frozen:
        recorder.freeze()
    compiler = Compiler(
        user_model=get_user_model(),
        query_compiler=PlanQueryCompiler(),
        path='tests.isolated.Registration',
        family='relationship',
    )
    recorder.play(compiler.get_recorder_target())
    return compiler.compile()


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
