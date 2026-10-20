"""Shared GH fixtures. Not authorization policy."""

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import Permission

from trusts.backends import TrustModelBackendMixin
from trusts.compile.catalogs import Catalog
from trusts.compile.factory import compile_recorder

from gh_permissions.backends import GhAuthorizationBackend
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


def permission_code(permission):
    """``app_label.codename`` for one saved ``auth.Permission``."""
    return '%s.%s' % (
        permission.content_type.app_label,
        permission.codename,
    )


def gh_catalog():
    """Catalog the ready-time sweep stored for the GH backend."""
    return GhAuthorizationBackend().get_catalog()


class IsolatedRegistration(object):
    """A fresh backend class and its still-open recorder."""

    def __init__(self):
        class Backend(TrustModelBackendMixin, BaseBackend):
            pass

        self.backend = Backend
        self.recorder = Backend().get_registry()


def catalog_for(registration):
    """Build ``Catalog(factory.build())`` from one isolated recorder."""
    shape = compile_recorder(
        registration.recorder,
        path='tests.isolated',
        family=registration.backend.family,
        compiler=registration.backend.query_compiler,
        user_model=get_user_model(),
    )
    return Catalog(shape)


def isolated_catalog(*registrars):
    """Register GH helpers on a new recorder and return its catalog."""
    registration = IsolatedRegistration()
    for registrar in registrars:
        registrar(registration.recorder)
    return catalog_for(registration)


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
