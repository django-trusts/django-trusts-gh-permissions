"""#9: the documented GH install authorizes through the live catalog."""

from django.conf import settings
from django.test import TestCase

from gh_permissions.models import Repository, TeamRepositoryPermission
from tests.fixtures import (
    GhFixtureMixin, gh_catalog, permission_code, policy_sql_text,
)


class ReadmeExampleAuthorizationTest(GhFixtureMixin, TestCase):
    def test_documented_settings_are_the_live_test_settings(self):
        # README's configure block is the object-authorization install.
        # The live settings add the admin harness and ModelBackend so
        # stock admin can authenticate and read no-object model permissions.
        self.assertEqual(
            settings.INSTALLED_APPS,
            (
                'django.contrib.contenttypes',
                'django.contrib.auth',
                'example.apps.ExampleConfig',
                'django.contrib.sessions',
                'django.contrib.messages',
                'django.contrib.staticfiles',
                'django.contrib.admin',
                'gh_permissions.apps.GhPermissionsConfig',
            ),
        )
        self.assertEqual(settings.AUTH_USER_MODEL, 'example.User')
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS,
            (
                'gh_permissions.backends.GhAuthorizationBackend',
                'django.contrib.auth.backends.ModelBackend',
            ),
        )
        self.assertNotIn('trusts', settings.INSTALLED_APPS)

    def test_documented_team_mapping_matches_startup_registration(self):
        record = gh_catalog().records[1]
        self.assertIs(record.root, TeamRepositoryPermission)
        self.assertEqual(record.user_path, ('team', 'members'))
        self.assertIn(
            'condition: "$permission.id in team__allowed_operations and '
            'team__organization == repository__organization"',
            policy_sql_text(),
        )

    def test_documented_object_and_listing_share_compiled_policy(self):
        registry = gh_catalog()
        self.assertTrue(
            registry.has_perm(
                self.member, permission_code(self.read), self.repo_a,
            ),
        )
        self.assertEqual(
            list(
                Repository.objects.authorized(self.member, self.read)
                .order_by('pk')
            ),
            [self.repo_a],
        )
        self.assertTrue(
            registry.has_perm(
                self.collaborator, permission_code(self.write), self.repo_b,
            ),
        )
        self.assertEqual(
            list(
                Repository.objects.authorized(self.collaborator, self.write)
                .order_by('pk')
            ),
            [self.repo_b],
        )
        self.assertFalse(
            registry.has_perm(
                self.member, permission_code(self.read), self.repo_b,
            ),
        )
