"""GH-only kernel topology: trusts_core, no Zero, inert trusts.models."""

import sys
from importlib import import_module

from django.apps import apps
from django.conf import settings
from django.core.checks import run_checks
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase

from trusts.apps import AppConfig, kernel_config
from trusts.query import AuthorizedManager

from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import Repository


class KernelIdentityTest(SimpleTestCase):
    def test_installed_apps_are_trusts_and_gh_permissions(self):
        labels = [config.label for config in apps.get_app_configs()]
        self.assertIn('trusts_core', labels)
        self.assertIn('gh_permissions', labels)
        self.assertNotIn('trusts', labels)
        names = [config.name for config in apps.get_app_configs()]
        self.assertIn('trusts', names)
        self.assertIn('gh_permissions', names)
        self.assertNotIn('trusts.zero', names)

    def test_kernel_config_is_class_identity_with_trusts_core_label(self):
        config = kernel_config()
        self.assertIs(type(config), AppConfig)
        self.assertEqual(config.name, 'trusts')
        self.assertEqual(config.label, 'trusts_core')
        self.assertIs(config, kernel_config())
        self.assertIs(config, apps.get_app_config('trusts_core'))
        with self.assertRaises(LookupError):
            apps.get_app_config('trusts')

    def test_kernel_exposes_no_concrete_models(self):
        config = kernel_config()
        self.assertEqual(list(config.get_models()), [])
        trusts = [m for m in apps.get_models() if m.__name__ == 'Trust']
        self.assertEqual(trusts, [])

    def test_no_trustmodelbackend_and_mixin_only_handle(self):
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS,
            ('gh_permissions.backends.GhAuthorizationBackend',),
        )
        from trusts.backends import TrustModelBackend, TrustModelBackendMixin
        self.assertTrue(issubclass(GhAuthorizationBackend, TrustModelBackendMixin))
        self.assertFalse(issubclass(GhAuthorizationBackend, TrustModelBackend))

    def test_repository_uses_authorized_manager(self):
        self.assertIsInstance(Repository.objects, AuthorizedManager)
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
        self.assertEqual(gh_keys, {('gh_permissions', '0001_initial')})


class ModelsShimTest(SimpleTestCase):
    def test_import_models_does_not_import_zero(self):
        self.assertNotIn('trusts.zero', sys.modules)
        module = import_module('trusts.models')
        self.assertIs(sys.modules['trusts.models'], module)
        self.assertNotIn('trusts.zero', sys.modules)
        self.assertNotIn('Trust', module.__dict__)

    def test_legacy_trust_import_raises_documented_error(self):
        self.assertNotIn('trusts.zero', sys.modules)
        with self.assertRaises(ImportError) as ctx:
            from trusts.models import Trust  # noqa: F401
        self.assertIn('django-trusts-zero', str(ctx.exception))
        self.assertIn("trusts.zero.apps.ZeroConfig", str(ctx.exception))
        self.assertNotIn('trusts.zero', sys.modules)

    def test_dir_hasattr_and_unknown_name_do_not_import_zero(self):
        self.assertNotIn('trusts.zero', sys.modules)
        module = import_module('trusts.models')
        names = dir(module)
        self.assertIn('Trust', names)
        self.assertIn('Content', names)
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
        config = kernel_config()
        self.assertEqual(list(config.get_models()), [])

    def test_system_checks_are_clean_and_zero_sql(self):
        self.assertNotIn('trusts.zero', sys.modules)
        with self.assertNumQueries(0):
            messages = [
                m for m in run_checks()
                if getattr(m, 'id', '').startswith('trusts.E')
            ]
        self.assertEqual(messages, [])
        self.assertNotIn('trusts.zero', sys.modules)
