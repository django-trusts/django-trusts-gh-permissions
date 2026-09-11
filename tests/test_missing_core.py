"""G1 stop: accepted team mapping must register on public C2 APIs.

This test is the required failing minimal evidence when live C2 cannot
express the team root. Do not add a consumer-local escape dialect.
"""

from django.test import TestCase

from trusts.core import TrustsRegistry

from gh_permissions.models import Repository, TeamRepoGrant
from gh_permissions.policy import register_team


class AcceptedTeamMappingTest(TestCase):
    def test_accepted_team_register_is_expressible_on_c2(self):
        """r7 §6 team record: membership hop + permission_in + Equal + All."""
        registry = TrustsRegistry()
        with self.assertNumQueries(0):
            record = register_team(registry)
        self.assertIs(record.root, TeamRepoGrant)
        self.assertIs(record.content_model, Repository)
        self.assertEqual(record.content_path, ('repository',))
        self.assertEqual(record.user_path, ('team', 'members'))
        self.assertEqual(record.permission_path, ('operation',))
        self.assertIsNotNone(record.condition)
        self.assertEqual(len(registry.records), 1)
