"""GH Issue #6 Step IIb proofs.

Locks the IIb contract against Step I core (``1.0.0.dev2`` /
merge ``39f1f961``):

* ``GhPermissionsConfig`` is the sole implementation owner
* mixin / ready / list execution never call ``kernel_config()``
* missing canonical backend path fails at startup
* no dependency on installed core AppConfig, ``trusts.core_backends``,
  the core historical backend, or Zero
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from trusts.apps import (
    AppConfig as CoreAppConfig,
    TrustsImplementationConfig,
    implementation_configs,
    implementation_for_path,
)
from gh_permissions.apps import CANONICAL_BACKEND, GhPermissionsConfig, gh_config
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import AccountRepoGrant, Repository, TeamRepoGrant


ROOT = Path(__file__).resolve().parents[1]


class Issue6StepIIbProofs(SimpleTestCase):
    def test_gh_config_is_sole_implementation_owner(self):
        owners = implementation_configs()
        self.assertEqual(len(owners), 1)
        self.assertIsInstance(owners[0], GhPermissionsConfig)
        self.assertIsInstance(owners[0], TrustsImplementationConfig)
        self.assertIs(owners[0], gh_config())
        self.assertNotIn('trusts_core', apps.app_configs)
        for config in apps.get_app_configs():
            self.assertFalse(type(config) is CoreAppConfig)

    def test_canonical_backend_resolves_only_through_owner(self):
        owner = implementation_for_path(CANONICAL_BACKEND)
        self.assertIs(owner, gh_config())
        self.assertEqual(owner.trusts_backend_paths, (CANONICAL_BACKEND,))
        handle = owner.configured_backend(CANONICAL_BACKEND)
        self.assertEqual(handle.path, CANONICAL_BACKEND)
        self.assertIs(
            type(handle.compiler),
            GhAuthorizationBackend.query_compiler.__class__,
        )

    def test_mixin_resolves_owner_without_kernel_config(self):
        def explode(*_args, **_kwargs):
            raise AssertionError('kernel_config() must not run when an owner is present')

        with patch('trusts.apps.kernel_config', side_effect=explode):
            backend = GhAuthorizationBackend()
            self.assertIs(backend._trusts_config(), gh_config())

    def test_ready_resolves_owner_without_kernel_config(self):
        def explode(*_args, **_kwargs):
            raise AssertionError('kernel_config() must not run from GhPermissionsConfig.ready()')

        with patch('trusts.apps.kernel_config', side_effect=explode):
            owner = implementation_for_path(CANONICAL_BACKEND)
            self.assertIs(owner, gh_config())
            roots = [
                record.root
                for record in owner.configured_backend(CANONICAL_BACKEND).registry.records
            ]
            self.assertEqual(roots, [AccountRepoGrant, TeamRepoGrant])

    def test_missing_canonical_backend_path_fails_at_startup(self):
        config = apps.get_app_config('gh_permissions')
        with override_settings(AUTHENTICATION_BACKENDS=[]):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                GhPermissionsConfig.ready(config)
        self.assertIn(CANONICAL_BACKEND, str(ctx.exception))

    def test_step_iii_readiness_guard_has_no_transitional_dependencies(self):
        import re

        banned = (
            'from trusts.apps import kernel_config',
            'from trusts.core_backends',
            'import trusts.core_backends',
            'from trusts.zero',
            'import trusts.zero',
            'django-trusts-zero',
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
        self.assertNotIn('trusts.apps.AppConfig', django_settings.INSTALLED_APPS)


class Issue6StepIIbAuthorizationProofs(TestCase):
    def test_list_authorized_resolves_owner_without_kernel_config(self):
        from gh_permissions.models import Account, Operation, Organization

        org = Organization.objects.create(name='iib-org')
        repo = Repository.objects.create(organization=org, title='iib-repo')
        account = Account.objects.create(name='iib-account')
        write = Operation.objects.create(code='iib-write')
        AccountRepoGrant.objects.create(
            account=account, repository=repo, operation=write,
        )

        def explode(*_args, **_kwargs):
            raise AssertionError('kernel_config() must not run from GH authorized()')

        with patch('trusts.apps.kernel_config', side_effect=explode):
            backend = GhAuthorizationBackend()
            self.assertIs(backend._trusts_config(), gh_config())
            listed = list(Repository.objects.authorized(account, write))
        self.assertIn(repo, listed)
