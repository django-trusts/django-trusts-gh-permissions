"""GH owner proofs on final core.

Locks the supported owner contract against ``django-trusts`` 1.x:

* ``GhPermissionsConfig`` is the sole implementation owner
* mixin / ready / list execution resolve through the owner API
* an unlisted catalog is ``trusts.E010``
* no dependency on ``kernel_config()``, a core ``AppConfig``,
  ``trusts.core_backends``, the removed core historical backend, or Zero
"""

from __future__ import annotations

import inspect
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase, override_settings

from trusts.apps import TrustsConfigMixin
from trusts.plan.relations import PlanQueryCompiler
from gh_permissions.apps import GhPermissionsConfig
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
    def test_gh_config_is_sole_implementation_owner(self):
        import trusts.apps as trusts_apps

        owners = [
            config for config in apps.get_app_configs()
            if isinstance(config, TrustsConfigMixin)
        ]
        self.assertEqual(len(owners), 1)
        self.assertIsInstance(owners[0], GhPermissionsConfig)
        self.assertIsInstance(owners[0], TrustsConfigMixin)
        self.assertIs(owners[0], apps.get_app_config('gh_permissions'))
        self.assertNotIn('trusts_core', apps.app_configs)
        self.assertFalse(hasattr(trusts_apps, 'AppConfig'))
        names = [config.name for config in apps.get_app_configs()]
        self.assertNotIn('trusts', names)

    def test_canonical_backend_resolves_only_through_owner(self):
        owner = apps.get_containing_app_config(GhAuthorizationBackend.__module__)
        self.assertIs(owner, apps.get_app_config('gh_permissions'))
        self.assertIn(
            'gh_permissions.backends.GhAuthorizationBackend',
            settings.AUTHENTICATION_BACKENDS,
        )
        backend = GhAuthorizationBackend()
        self.assertIs(
            backend.catalog,
            apps.get_app_config('gh_permissions').get_catalog(),
        )
        self.assertIsInstance(backend.catalog.compiler, PlanQueryCompiler)

    def test_mixin_resolves_owner_without_removed_core_surfaces(self):
        import trusts.apps as trusts_apps

        self.assertFalse(hasattr(trusts_apps, 'kernel_config'))
        backend = GhAuthorizationBackend()
        self.assertIs(
            backend.catalog,
            apps.get_app_config('gh_permissions').get_catalog(),
        )
        self.assertIs(
            apps.get_containing_app_config(type(backend).__module__),
            apps.get_app_config('gh_permissions'),
        )

    def test_ready_resolves_owner_without_removed_core_surfaces(self):
        import trusts.apps as trusts_apps

        self.assertFalse(hasattr(trusts_apps, 'kernel_config'))
        self.assertIs(
            apps.get_containing_app_config(GhAuthorizationBackend.__module__),
            apps.get_app_config('gh_permissions'),
        )
        roots = [
            record.root
            for record in apps.get_app_config('gh_permissions').get_catalog().records
        ]
        self.assertEqual(roots, [
            RepositoryCollaborator,
            TeamRepositoryPermission,
            OrganizationOwnership,
            OrganizationOwnership,
            IssuePermissions,
        ])

    def test_unlisted_catalog_is_trusts_e010(self):
        with override_settings(AUTHENTICATION_BACKENDS=[]):
            messages = [
                message for message in run_checks()
                if getattr(message, 'id', None) == 'trusts.E010'
            ]
        self.assertEqual(len(messages), 1)
        self.assertIn('gh_permissions', messages[0].msg)

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
        self.assertIn('register_collaborator(self.recorder)', ready)
        self.assertIn('register_team(self.recorder)', ready)
        self.assertIn('register_organization_owner(self.recorder)', ready)
        self.assertNotIn('_gh_policy_registry_id', ready)
        self.assertNotIn('.registry.register(', ready)
        self.assertNotIn('register_collaborator(registry)', ready)
        self.assertNotIn('register_team(registry)', ready)
        self.assertNotIn('register_organization_owner(registry)', ready)


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
        backend = GhAuthorizationBackend()
        self.assertIs(
            backend.catalog,
            apps.get_app_config('gh_permissions').get_catalog(),
        )
        listed = list(Repository.objects.authorized(user, write))
        self.assertIn(repo, listed)
