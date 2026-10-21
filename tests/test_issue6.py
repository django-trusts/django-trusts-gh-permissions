"""GH startup proofs on the app-owned policy.

* ``GhPermissionsConfig`` records relations and publishes the policy
* a missing canonical backend path fails at ``ready()``
* a second ``ready()`` fails closed
* the listed backend authorizes from that policy
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase, override_settings

from trusts.apps import TrustsConfigMixin
from trusts.backends import policy_document
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
from tests.fixtures import gh_trusts, repository_permission


class Issue6OwnerProofs(SimpleTestCase):
    def test_gh_config_is_the_trusts_mixin(self):
        owner = gh_config()
        self.assertIsInstance(owner, GhPermissionsConfig)
        self.assertIsInstance(owner, TrustsConfigMixin)
        self.assertNotIn('trusts_core', apps.app_configs)
        names = [config.name for config in apps.get_app_configs()]
        self.assertNotIn('trusts', names)
        self.assertNotIn('trusts.apps', names)

    def test_policy_document_names_the_gh_config(self):
        direct = GhAuthorizationBackend().policy_document()
        project = policy_document()
        named = policy_document(
            backend='gh_permissions.backends.GhAuthorizationBackend',
        )
        self.assertEqual(direct, project)
        self.assertEqual(direct, named)
        self.assertEqual(direct['schema_version'], 1)
        self.assertEqual(
            [row['path'] for row in direct['backends']],
            ['gh_permissions.apps.GhPermissionsConfig'],
        )
        self.assertEqual(
            direct['database']['engine'],
            settings.DATABASES['default']['ENGINE'],
        )

    def test_ready_registers_ordinary_roots(self):
        roots = [trust['root'] for _model, trust in gh_trusts()]
        self.assertEqual(roots, [
            RepositoryCollaborator._meta.label,
            TeamRepositoryPermission._meta.label,
            OrganizationOwnership._meta.label,
            OrganizationOwnership._meta.label,
            IssuePermissions._meta.label,
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
    def test_list_authorized_sees_the_collaborator(self):
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

        listed = list(Repository.objects.authorized(user, write))
        self.assertIn(repo, listed)
        self.assertTrue(
            GhAuthorizationBackend().has_perm(
                user, 'gh_permissions.write_repository', repo,
            ),
        )
