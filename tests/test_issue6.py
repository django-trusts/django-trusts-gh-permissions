"""GH startup proofs on the catalog sweep.

* ``GhPermissionsConfig`` records GH relations before the sweep
* ``trusts.apps.TrustsImplementationConfig`` builds the catalog
* a missing canonical backend path fails at ``ready()``
* no dependency on ``kernel_config()``, ``trusts.core``,
  ``implementation_for_path``, or Zero
"""

from __future__ import annotations

import inspect
from pathlib import Path

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from trusts.apps import TrustsImplementationConfig
from gh_permissions.apps import CANONICAL_BACKEND, GhPermissionsConfig, gh_config
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import (
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    IssuePermissions,
    TeamRepositoryPermission,
)


ROOT = Path(__file__).resolve().parents[1]


class Issue6OwnerProofs(SimpleTestCase):
    def test_gh_config_registers_before_the_installed_sweep(self):
        import trusts.apps as trusts_apps

        self.assertIsInstance(gh_config(), GhPermissionsConfig)
        self.assertNotIsInstance(gh_config(), TrustsImplementationConfig)
        lifecycle = apps.get_app_config('trusts_lifecycle')
        self.assertIsInstance(lifecycle, TrustsImplementationConfig)
        self.assertNotIn('trusts_core', apps.app_configs)
        self.assertFalse(hasattr(trusts_apps, 'AppConfig'))
        names = [config.name for config in apps.get_app_configs()]
        self.assertNotIn('trusts', names)
        self.assertIn('trusts.apps', names)

    def test_canonical_backend_catalog_uses_the_mixin_compiler(self):
        catalog = GhAuthorizationBackend().get_catalog()
        self.assertEqual(catalog.path, CANONICAL_BACKEND)
        self.assertIs(
            type(catalog.compiler),
            GhAuthorizationBackend.query_compiler.__class__,
        )
        self.assertTrue(callable(catalog.compiler.applies))

    def test_ready_registers_ordinary_roots(self):
        import trusts.apps as trusts_apps

        self.assertFalse(hasattr(trusts_apps, 'kernel_config'))
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

    def test_source_has_no_transitional_dependencies(self):
        import re

        banned = (
            'from trusts.apps import kernel_config',
            'from trusts.core_backends',
            'import trusts.core_backends',
            'from trusts.zero',
            'import trusts.zero',
            'django-trusts-zero',
            'implementation_for_path',
            'from trusts.core',
            'BackendHandle',
        )
        historical = re.compile(
            r'from trusts\.backends import TrustModelBackend(?!Mixin)'
        )
        offenders = []
        for path in (ROOT / 'gh_permissions').rglob('*.py'):
            text = path.read_text()
            for needle in banned:
                if needle in text:
                    offenders.append('%s: %s' % (path.relative_to(ROOT), needle))
            if historical.search(text):
                offenders.append(
                    '%s: from trusts.backends import TrustModelBackend'
                    % path.relative_to(ROOT)
                )
        self.assertEqual(offenders, [])
        from django.conf import settings as django_settings

        self.assertNotIn('trusts', django_settings.INSTALLED_APPS)
        self.assertIn(
            'trusts.apps.TrustsImplementationConfig',
            django_settings.INSTALLED_APPS,
        )
        self.assertNotIn('trusts.apps.AppConfig', django_settings.INSTALLED_APPS)
        ready = Path(inspect.getfile(GhPermissionsConfig)).read_text()
        self.assertIn('register_collaborator(recorder)', ready)
        self.assertIn('register_team(recorder)', ready)
        self.assertIn('register_organization_owner(recorder)', ready)
        self.assertNotIn('_gh_policy_registry_id', ready)
        self.assertNotIn('.registry.register(', ready)
        self.assertNotIn('register_collaborator(registry)', ready)
        self.assertNotIn('register_team(registry)', ready)
        self.assertNotIn('register_organization_owner(registry)', ready)
        self.assertNotIn('register_collaborator(handle)', ready)


class Issue6AuthorizationProofs(TestCase):
    def test_list_authorized_resolves_owner_without_removed_core_surfaces(self):
        import trusts.apps as trusts_apps
        from django.contrib.auth import get_user_model

        from gh_permissions.models import Organization
        from gh_permissions.services import ensure_owner_group

        from tests.fixtures import repository_permission

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

        self.assertFalse(hasattr(trusts_apps, 'kernel_config'))
        catalog = GhAuthorizationBackend().get_catalog()
        self.assertEqual(catalog.path, CANONICAL_BACKEND)
        listed = list(Repository.objects.authorized(user, write))
        self.assertIn(repo, listed)
