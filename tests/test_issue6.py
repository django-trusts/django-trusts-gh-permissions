"""GH startup proofs on the app-owned catalog.

* ``GhPermissionsConfig`` records relations and builds the catalog
* a missing canonical backend path fails at ``ready()``
* the listed backend reads that same catalog
"""

from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

import trusts.apps as trusts_apps
from trusts.apps import TrustsConfigMixin
from trusts.errors import TrustsConfigurationError
from gh_permissions.apps import CANONICAL_BACKEND, GhPermissionsConfig, gh_config
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import (
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    IssuePermissions,
    TeamRepositoryPermission,
)
from gh_permissions.services import ensure_owner_group
from tests.fixtures import repository_permission


class Issue6OwnerProofs(SimpleTestCase):
    def test_gh_config_owns_the_catalog(self):
        owner = gh_config()
        self.assertIsInstance(owner, GhPermissionsConfig)
        self.assertIsInstance(owner, TrustsConfigMixin)
        self.assertNotIn('trusts_core', apps.app_configs)
        self.assertFalse(hasattr(trusts_apps, 'AppConfig'))
        names = [config.name for config in apps.get_app_configs()]
        self.assertNotIn('trusts', names)
        self.assertNotIn('trusts.apps', names)
        catalog = GhAuthorizationBackend().get_catalog()
        self.assertIs(catalog, owner.get_catalog())
        self.assertEqual(catalog.path, owner.catalog_path())

    def test_canonical_backend_catalog_path_and_family(self):
        catalog = GhAuthorizationBackend().get_catalog()
        self.assertEqual(catalog.path, gh_config().catalog_path())
        self.assertEqual(catalog.family, 'relationship')

    def test_ready_registers_ordinary_roots(self):
        roots = [
            record.root
            for record in GhAuthorizationBackend().get_catalog().records
        ]
        self.assertEqual(roots, [
            RepositoryCollaborator,
            TeamRepositoryPermission,
            OrganizationOwnership,
            OrganizationOwnership,
            IssuePermissions,
        ])

    def test_missing_canonical_backend_path_fails_at_startup(self):
        config = apps.get_app_config('gh_permissions')
        with override_settings(AUTHENTICATION_BACKENDS=[]):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                GhPermissionsConfig.ready(config)
        self.assertIn(CANONICAL_BACKEND, str(ctx.exception))

    def test_second_ready_fails_on_the_frozen_recorder(self):
        config = apps.get_app_config('gh_permissions')
        with self.assertRaises(TrustsConfigurationError):
            GhPermissionsConfig.ready(config)


class Issue6AuthorizationProofs(TestCase):
    def test_list_authorized_uses_the_app_catalog(self):
        org = Organization.objects.create(
            name='iib-org', owner_group=ensure_owner_group(),
        )
        repo = Repository.objects.create(organization=org, name='iib-repo')
        user = get_user_model().objects.create(username='iib-account')
        write = repository_permission('write_repository')
        collaboration = RepositoryCollaborator.objects.create(
            user=user, repository=repo,
        )
        collaboration.permissions.add(write)

        catalog = GhAuthorizationBackend().get_catalog()
        self.assertIs(catalog, gh_config().get_catalog())
        self.assertEqual(catalog.path, gh_config().catalog_path())
        listed = list(Repository.objects.authorized(user, write))
        self.assertIn(repo, listed)
