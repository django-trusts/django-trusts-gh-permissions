"""#9: the documented GH install authorizes through the live catalog."""

from django.test import TestCase

from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import Repository, TeamRepositoryPermission
from tests.fixtures import GhFixtureMixin, gh_trusts, permission_code


class ReadmeExampleAuthorizationTest(GhFixtureMixin, TestCase):
    def test_documented_team_mapping_matches_startup_registration(self):
        _model, trust = gh_trusts()[1]
        self.assertEqual(trust['root'], TeamRepositoryPermission._meta.label)
        self.assertEqual(trust['user']['path'], 'team__members')
        self.assertEqual(
            trust['condition'],
            '$permission.id in team__allowed_operations and '
            'team__organization == repository__organization',
        )

    def test_documented_object_and_listing_share_compiled_policy(self):
        backend = GhAuthorizationBackend()
        self.assertTrue(
            backend.has_perm(
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
            backend.has_perm(
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
            backend.has_perm(
                self.member, permission_code(self.read), self.repo_b,
            ),
        )
