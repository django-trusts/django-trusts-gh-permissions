"""GH kernel topology: one app-owned catalog, no Core app, no Zero."""

import importlib.util
import sys
from importlib import import_module

from django.apps import apps
from django.conf import settings
from django.core.checks import run_checks
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase

import trusts.apps as trusts_apps
import trusts.backends as core_backends
from trusts.apps import TrustsConfigMixin
from trusts.backends import TrustModelBackendMixin
from trusts.models import AuthorizedManager, AuthorizedQuerySet

import gh_permissions.models as gh_models
from gh_permissions.apps import GhPermissionsConfig, gh_config
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import Repository


class KernelIdentityTest(SimpleTestCase):
    def test_installed_apps_are_gh_only(self):
        labels = [config.label for config in apps.get_app_configs()]
        self.assertIn('gh_permissions', labels)
        self.assertNotIn('trusts_lifecycle', labels)
        self.assertNotIn('trusts_core', labels)
        names = [config.name for config in apps.get_app_configs()]
        self.assertIn('gh_permissions', names)
        self.assertNotIn('trusts.apps', names)
        self.assertNotIn('trusts', names)
        self.assertNotIn('trusts.zero', names)

    def test_core_ships_no_installed_app(self):
        self.assertFalse(hasattr(trusts_apps, 'AppConfig'))
        self.assertNotIn('trusts_core', apps.app_configs)
        names = [config.name for config in apps.get_app_configs()]
        self.assertNotIn('trusts', names)
        self.assertNotIn('trusts.apps', names)

    def test_gh_catalog_belongs_to_gh_permissions_config(self):
        owner = gh_config()
        self.assertIsInstance(owner, GhPermissionsConfig)
        self.assertIsInstance(owner, TrustsConfigMixin)
        catalog = GhAuthorizationBackend().get_catalog()
        self.assertIs(catalog, owner.get_catalog())
        self.assertEqual(catalog.path, owner.catalog_path())
        self.assertEqual(catalog.family, 'relationship')

    def test_no_trustmodelbackend_and_mixin_only_handle(self):
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS,
            (
                'gh_permissions.backends.GhAuthorizationBackend',
                'django.contrib.auth.backends.ModelBackend',
            ),
        )
        self.assertTrue(issubclass(GhAuthorizationBackend, TrustModelBackendMixin))
        self.assertFalse(hasattr(core_backends, 'TrustModelBackend'))
        with self.assertRaises(ImportError):
            # TrustModelBackend was removed. This import has to fail here.
            from trusts.backends import TrustModelBackend  # noqa: F401

    def test_repository_uses_stock_authorized_manager(self):
        self.assertIs(type(Repository.objects), AuthorizedManager)
        self.assertIs(Repository.objects._queryset_class, AuthorizedQuerySet)
        self.assertFalse(hasattr(gh_models, 'GhAuthorizedQuerySet'))
        self.assertFalse(hasattr(gh_models, 'GhAuthorizedManager'))
        self.assertFalse(hasattr(Repository.objects, 'permitted'))
        self.assertFalse(hasattr(Repository.objects, 'get_permission'))


class KernelMigrationLoaderTest(TestCase):
    def test_kernel_has_no_trusts_migration_keys(self):
        loader = MigrationLoader(connection)
        keys = {key for key in loader.disk_migrations if key[0] == 'trusts'}
        self.assertEqual(keys, set())
        core_keys = {
            key for key in loader.disk_migrations if key[0] == 'trusts_core'
        }
        self.assertEqual(core_keys, set())
        gh_keys = {
            key for key in loader.disk_migrations if key[0] == 'gh_permissions'
        }
        self.assertEqual(
            gh_keys,
            {
                ('gh_permissions', '0001_initial'),
                ('gh_permissions', '0002_auth_permission_terminal'),
                ('gh_permissions', '0003_organization_owner_permission'),
                ('gh_permissions', '0004_shared_names_ownership_collaborators'),
                ('gh_permissions', '0005_repositorydelegation'),
                ('gh_permissions', '0006_personal_delegations'),
                (
                    'gh_permissions',
                    '0007_issue_issuepermissions_and_more',
                ),
                (
                    'gh_permissions',
                    '0008_rename_delegation_permissions',
                ),
            },
        )


class ModelsShimTest(SimpleTestCase):
    def test_import_models_does_not_import_zero(self):
        self.assertNotIn('trusts.zero', sys.modules)
        module = import_module('trusts.models')
        self.assertIs(sys.modules['trusts.models'], module)
        self.assertNotIn('trusts.zero', sys.modules)
        self.assertNotIn('Trust', module.__dict__)
        self.assertNotIn('Content', module.__dict__)

    def test_legacy_trust_import_does_not_load_zero(self):
        if importlib.util.find_spec('trusts.zero') is not None:
            self.skipTest('Zero is present on this interpreter; CI pair is GH-only')
        self.assertNotIn('trusts.zero', sys.modules)
        with self.assertRaises(ImportError):
            # Trust was removed from trusts.models. This import has to fail here.
            from trusts.models import Trust  # noqa: F401
        self.assertNotIn('trusts.zero', sys.modules)

    def test_dir_hasattr_and_unknown_name_do_not_import_zero(self):
        self.assertNotIn('trusts.zero', sys.modules)
        module = import_module('trusts.models')
        names = dir(module)
        self.assertNotIn('Trust', names)
        self.assertNotIn('Content', names)
        self.assertNotIn('trusts.zero', sys.modules)
        self.assertFalse(hasattr(module, 'anything'))
        self.assertFalse(hasattr(module, 'NotALegacyModel'))
        self.assertNotIn('trusts.zero', sys.modules)
        with self.assertRaises(AttributeError) as ctx:
            getattr(module, 'NotALegacyModel')
        self.assertIn('NotALegacyModel', str(ctx.exception))
        self.assertNotIn('ImportError', type(ctx.exception).__name__)
        self.assertNotIn('trusts.zero', sys.modules)


class GhStartupWithoutZeroTest(TestCase):
    def test_gh_repository_loads_and_zero_stays_unimported(self):
        self.assertNotIn('trusts.zero', sys.modules)
        self.assertEqual(Repository._meta.app_label, 'gh_permissions')
        self.assertIs(
            apps.get_model('gh_permissions', 'Repository'),
            Repository,
        )
        self.assertTrue(apps.is_installed('gh_permissions'))
        self.assertNotIn('trusts.zero', sys.modules)
        self.assertIn(Repository, list(gh_config().get_models()))

    def test_system_checks_are_clean_and_zero_sql(self):
        self.assertNotIn('trusts.zero', sys.modules)
        with self.assertNumQueries(0):
            messages = [
                m for m in run_checks()
                if getattr(m, 'id', '').startswith('trusts.E')
            ]
        self.assertEqual(messages, [])
        self.assertNotIn('trusts.zero', sys.modules)
