"""#6 Step IIb: GH-owned AppConfig registry lifecycle on merged Step I."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase

from trusts.apps import (
    implementation_for_class,
    implementation_for_path,
    kernel_config,
)
from trusts.core import TrustsConfigurationError

from gh_permissions.apps import (
    CANONICAL_BACKEND_PATH,
    FLOOR_MESSAGE,
    GhPermissionsConfig,
    gh_config,
)
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import AccountRepoGrant, Repository, TeamRepoGrant
from tests.fixtures import GhFixtureMixin, gh_registry


ROOT = Path(__file__).resolve().parents[1]
KERNEL = Path(
    os.environ.get('KERNEL_CHECKOUT', ROOT / '.deps' / 'django-trusts')
).resolve()

STARTUP_PROBE = r'''
import os, sys
from pathlib import Path
root = Path(%r).resolve()
kernel = Path(%r).resolve()
sys.path.insert(0, str(kernel))
sys.path.append(str(root))
os.environ.pop("DJANGO_SETTINGS_MODULE", None)
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
settings.configure(
    SECRET_KEY="iib-startup",
    USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    INSTALLED_APPS=[
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "gh_permissions",
    ],
    AUTHENTICATION_BACKENDS=%r,
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
)
import django
try:
    django.setup()
except ImproperlyConfigured as exc:
    print("improperly-configured", exc)
    raise SystemExit(0)
print("populate-succeeded")
raise SystemExit(1)
'''


class CanonicalBackendIdentityTests(SimpleTestCase):
    def test_canonical_path_resolves_only_through_gh_config(self):
        owner = implementation_for_path(CANONICAL_BACKEND_PATH)
        self.assertIs(type(owner), GhPermissionsConfig)
        self.assertIs(owner, gh_config())
        self.assertIs(implementation_for_class(GhAuthorizationBackend), owner)
        handle = owner.configured_backend(CANONICAL_BACKEND_PATH)
        self.assertEqual(handle.path, CANONICAL_BACKEND_PATH)
        self.assertIs(handle.registry, owner.registries[CANONICAL_BACKEND_PATH])

    def test_core_historical_class_is_not_gh_identity(self):
        from trusts.backends import TrustModelBackend as CoreHistoricalBackend

        with self.assertRaises(TrustsConfigurationError):
            implementation_for_class(CoreHistoricalBackend)


class OwnerPresentNeverCallsKernelTests(SimpleTestCase):
    def test_gh_config_ready_does_not_call_kernel_config(self):
        owner = gh_config()
        with patch('trusts.apps.kernel_config') as kernel:
            owner.ready()
            kernel.assert_not_called()

    def test_mixin_owner_present_never_calls_kernel_config(self):
        backend = GhAuthorizationBackend()
        with patch('trusts.apps.kernel_config') as kernel:
            config = backend._trusts_config()
            self.assertIs(type(config), GhPermissionsConfig)
            self.assertIs(config, gh_config())
            kernel.assert_not_called()
            handle = backend._own_handle()
            self.assertEqual(handle.path, CANONICAL_BACKEND_PATH)

    def test_gh_config_helper_does_not_call_kernel_config(self):
        with patch('trusts.apps.kernel_config') as kernel:
            self.assertIs(type(gh_config()), GhPermissionsConfig)
            kernel.assert_not_called()

    def test_live_kernel_config_is_absent(self):
        with self.assertRaises(LookupError):
            kernel_config()


class OwnerPresentProjectionNeverCallsKernelTests(GhFixtureMixin, TestCase):
    def test_list_object_enumeration_never_call_kernel_config(self):
        registry = gh_registry()
        with patch('trusts.apps.kernel_config') as kernel:
            self.assertTrue(
                registry.has_permission(
                    self.member, self.repo_a, self.read,
                )
            )
            listed = list(
                registry.filter_authorized(
                    Repository.objects.all(), self.member, self.read,
                )
            )
            enumerated = list(
                registry.permissions_for(self.member, self.repo_a)
            )
            kernel.assert_not_called()
        self.assertEqual(listed, [self.repo_a])
        self.assertEqual(
            {row.pk for row in enumerated}, {self.read.pk},
        )


class StartupBeltTests(SimpleTestCase):
    def test_missing_step_i_helper_is_improperly_configured(self):
        import gh_permissions.apps as gh_apps

        config = apps.get_app_config('gh_permissions')
        with patch.object(gh_apps, 'TrustsImplementationConfig', None):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                config.ready()
        self.assertIn('1.0.0.dev2', str(ctx.exception))
        self.assertIn('TrustsImplementationConfig', str(ctx.exception))
        self.assertEqual(str(ctx.exception), FLOOR_MESSAGE)

    def test_missing_canonical_path_fails_startup(self):
        result = subprocess.run(
            [
                sys.executable, '-c',
                STARTUP_PROBE % (
                    str(ROOT), str(KERNEL),
                    ['django.contrib.auth.backends.ModelBackend'],
                ),
            ],
            cwd=str(ROOT),
            env={
                k: v for k, v in os.environ.items()
                if k != 'DJANGO_SETTINGS_MODULE'
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('improperly-configured', result.stdout)
        self.assertIn(CANONICAL_BACKEND_PATH, result.stdout)
        self.assertNotIn('populate-succeeded', result.stdout)

    def test_canonical_settings_start(self):
        result = subprocess.run(
            [
                sys.executable, '-c',
                STARTUP_PROBE % (
                    str(ROOT), str(KERNEL),
                    [CANONICAL_BACKEND_PATH],
                ),
            ],
            cwd=str(ROOT),
            env={
                k: v for k, v in os.environ.items()
                if k != 'DJANGO_SETTINGS_MODULE'
            },
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('populate-succeeded', result.stdout)


class IndependentRootRegistrationTests(SimpleTestCase):
    def test_startup_registers_each_root_exactly_once(self):
        registry = gh_registry()
        roots = [record.root for record in registry.records]
        self.assertEqual(roots, [AccountRepoGrant, TeamRepoGrant])
        owner = gh_config()
        owner.ready()
        self.assertEqual(
            [record.root for record in registry.records],
            [AccountRepoGrant, TeamRepoGrant],
        )
        self.assertIs(
            owner.registries[CANONICAL_BACKEND_PATH], registry,
        )


class StepIIIReadinessGuardTests(SimpleTestCase):
    """GH IIb must not depend on surfaces Step III deletes.

    A real Step-III pair remains required before core Step III merges:
    Step I ``AuthorizedQuerySet.authorized`` still uses the kernel
    accessor. GH package code does not.
    """

    FORBIDDEN_IN_PACKAGE = (
        'from trusts.apps import kernel_config',
        'import trusts.core_backends',
        'from trusts.core_backends',
        'from trusts.backends import TrustModelBackend\n',
        'trusts.zero',
        'django-trusts-zero',
    )

    def test_package_source_has_no_step_iii_dependencies(self):
        package = ROOT / 'gh_permissions'
        offenders = []
        for path in package.rglob('*.py'):
            text = path.read_text()
            for needle in self.FORBIDDEN_IN_PACKAGE:
                if needle in text:
                    offenders.append(
                        '%s: %s' % (path.relative_to(ROOT), needle)
                    )
            if 'kernel_config(' in text:
                offenders.append(
                    '%s: kernel_config(' % path.relative_to(ROOT)
                )
        self.assertEqual(offenders, [])

    def test_settings_have_no_core_app_or_zero(self):
        from django.conf import settings

        self.assertNotIn('trusts', settings.INSTALLED_APPS)
        self.assertNotIn('trusts.zero', settings.INSTALLED_APPS)
        self.assertNotIn(
            'trusts.backends.TrustModelBackend',
            settings.AUTHENTICATION_BACKENDS,
        )
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS,
            (CANONICAL_BACKEND_PATH,),
        )

    def test_no_installed_trusts_appconfig_or_zero_modules(self):
        import importlib.util

        self.assertFalse(apps.is_installed('trusts'))
        labels = [config.label for config in apps.get_app_configs()]
        self.assertNotIn('trusts_core', labels)
        self.assertNotIn('trusts.zero', sys.modules)
        try:
            spec = importlib.util.find_spec('trusts.zero')
        except ModuleNotFoundError:
            spec = None
        self.assertIsNone(spec)
