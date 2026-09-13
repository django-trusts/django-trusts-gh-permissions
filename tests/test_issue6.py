"""GH owner proofs on final core.

Locks the supported owner contract against ``django-trusts`` 1.x:

* ``GhPermissionsConfig`` is the sole implementation owner
* mixin / ready / list execution resolve through the owner API
* missing canonical backend path fails at startup
* no dependency on ``kernel_config()``, a core ``AppConfig``,
  ``trusts.core_backends``, the removed core historical backend, or Zero
"""

from __future__ import annotations

import inspect
from pathlib import Path

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from trusts.apps import (
    TrustsImplementationConfig,
    implementation_configs,
    implementation_for_path,
)
from gh_permissions.apps import CANONICAL_BACKEND, GhPermissionsConfig, gh_config
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import (
    Repository,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)


ROOT = Path(__file__).resolve().parents[1]


class Issue6OwnerProofs(SimpleTestCase):
    def test_gh_config_is_sole_implementation_owner(self):
        import trusts.apps as trusts_apps

        owners = implementation_configs()
        self.assertEqual(len(owners), 1)
        self.assertIsInstance(owners[0], GhPermissionsConfig)
        self.assertIsInstance(owners[0], TrustsImplementationConfig)
        self.assertIs(owners[0], gh_config())
        self.assertNotIn('trusts_core', apps.app_configs)
        self.assertFalse(hasattr(trusts_apps, 'AppConfig'))
        names = [config.name for config in apps.get_app_configs()]
        self.assertNotIn('trusts', names)

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

    def test_mixin_resolves_owner_without_removed_core_surfaces(self):
        import trusts.apps as trusts_apps

        self.assertFalse(hasattr(trusts_apps, 'kernel_config'))
        backend = GhAuthorizationBackend()
        self.assertIs(backend._trusts_config(), gh_config())

    def test_ready_resolves_owner_without_removed_core_surfaces(self):
        import trusts.apps as trusts_apps

        self.assertFalse(hasattr(trusts_apps, 'kernel_config'))
        owner = implementation_for_path(CANONICAL_BACKEND)
        self.assertIs(owner, gh_config())
        roots = [
            record.root
            for record in owner.configured_backend(CANONICAL_BACKEND).registry.records
        ]
        self.assertEqual(roots, [UserRepositoryPermission, TeamRepositoryPermission])

    def test_ready_reentry_is_handle_keyed(self):
        from django.apps import apps

        config = apps.get_app_config('gh_permissions')
        owner = implementation_for_path(CANONICAL_BACKEND)
        handle = owner.configured_backend(CANONICAL_BACKEND)
        before = handle.registry.records
        self.assertEqual(
            [record.root for record in before],
            [UserRepositoryPermission, TeamRepositoryPermission],
        )
        GhPermissionsConfig.ready(config)
        after = owner.configured_backend(CANONICAL_BACKEND).registry.records
        self.assertEqual(after, before)

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
        ready = Path(inspect.getfile(GhPermissionsConfig)).read_text()
        self.assertIn('register_direct(handle)', ready)
        self.assertIn('register_team(handle)', ready)
        self.assertNotIn('_gh_policy_registry_id', ready)
        self.assertIn('_gh_policy_handle_id', ready)
        self.assertIn('== handle', ready)
        self.assertNotIn('.registry.register(', ready)
        self.assertNotIn('register_direct(registry)', ready)
        self.assertNotIn('register_team(registry)', ready)


class Issue6AuthorizationProofs(TestCase):
    def test_list_authorized_resolves_owner_without_removed_core_surfaces(self):
        import trusts.apps as trusts_apps
        from django.contrib.auth import get_user_model

        from gh_permissions.models import Operation, Organization

        org = Organization.objects.create(name='iib-org')
        repo = Repository.objects.create(organization=org, title='iib-repo')
        user = get_user_model().objects.create(username='iib-account')
        write = Operation.objects.create(code='iib-write')
        UserRepositoryPermission.objects.create(
            user=user, repository=repo, operation=write,
        )

        self.assertFalse(hasattr(trusts_apps, 'kernel_config'))
        backend = GhAuthorizationBackend()
        self.assertIs(backend._trusts_config(), gh_config())
        listed = list(Repository.objects.authorized(user, write))
        self.assertIn(repo, listed)
