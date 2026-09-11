"""GH IIb topology: one implementation owner, no core AppConfig, no Zero."""

import sys
from importlib import import_module

from django.apps import apps
from django.conf import settings
from django.core.checks import run_checks
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase

from trusts.apps import (
    AppConfig as KernelAppConfig,
    TrustsImplementationConfig,
    implementation_configs,
    implementation_for_class,
    implementation_for_path,
    kernel_config,
)
from trusts.query import AuthorizedManager

from gh_permissions.apps import (
    CANONICAL_BACKEND_PATH,
    GhPermissionsConfig,
    gh_config,
)
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import Repository


class KernelIdentityTest(SimpleTestCase):
    def test_installed_apps_are_gh_permissions_only(self):
        labels = [config.label for config in apps.get_app_configs()]
        self.assertIn('gh_permissions', labels)
        self.assertNotIn('trusts_core', labels)
        self.assertNotIn('trusts', labels)
        names = [config.name for config in apps.get_app_configs()]
        self.assertIn('gh_permissions', names)
        self.assertNotIn('trusts', names)
        self.assertNotIn('trusts.zero', names)

    def test_exactly_one_implementation_owner_and_no_core_appconfig(self):
        self.assertTrue(issubclass(GhPermissionsConfig, TrustsImplementationConfig))
        owners = implementation_configs()
        self.assertEqual(len(owners), 1)
        self.assertIs(owners[0], gh_config())
        self.assertIs(owners[0], apps.get_app_config('gh_permissions'))
        self.assertEqual(
            owners[0].trusts_backend_paths, (CANONICAL_BACKEND_PATH,),
        )
        kernelish = [
            config for config in apps.get_app_configs()
            if type(config) is KernelAppConfig
        ]
        self.assertEqual(kernelish, [])
        with self.assertRaises(LookupError) as ctx:
            kernel_config()
        self.assertIn('No installed Trusts kernel AppConfig', str(ctx.exception))

    def test_gh_authorization_backend_resolves_only_through_owner(self):
        owner = implementation_for_path(CANONICAL_BACKEND_PATH)
        self.assertIs(type(owner), GhPermissionsConfig)
        self.assertIs(owner, gh_config())
        self.assertIs(implementation_for_class(GhAuthorizationBackend), owner)
        handle = owner.configured_backend(CANONICAL_BACKEND_PATH)
        self.assertEqual(handle.path, CANONICAL_BACKEND_PATH)
        self.assertIs(handle.registry, owner.registries[CANONICAL_BACKEND_PATH])

    def test_no_trust_model_and_mixin_only_handle(self):
        trusts = [m for m in apps.get_models() if m.__name__ == 'Trust']
        self.assertEqual(trusts, [])
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
        self.assertFalse(apps.is_installed('trusts'))
        self.assertNotIn('trusts.zero', sys.modules)

    def test_system_checks_are_clean_and_zero_sql(self):
        self.assertNotIn('trusts.zero', sys.modules)
        with self.assertNumQueries(0):
            messages = [
                m for m in run_checks()
                if getattr(m, 'id', '').startswith('trusts.E')
            ]
        self.assertEqual(messages, [])
        self.assertNotIn('trusts.zero', sys.modules)
