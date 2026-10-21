"""Accepted team mapping is expressible through ``recorder.register``.

django-trusts#54 r7 / issue #3 accepted team registration, flattened
ceiling after #12, public Django ``__`` paths after #131, the
relationship method after Core #174, and the public ``register`` plus
symbolic condition after Core #211. The compiled condition is the
lockfile text. Django 6.1 defines ``assertNumQueries`` on
``TransactionTestCase`` (and therefore ``TestCase``), not
``SimpleTestCase``.
"""

from django.test import TestCase

from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import Repository, TeamRepositoryPermission
from gh_permissions.policy import (
    register_collaborator,
    register_organization_owner,
    register_team,
)
from tests.fixtures import (
    GhFixtureMixin,
    gh_catalog,
    permission_code,
    policy_sql_text,
)


def _team_record():
    return next(
        record for record in gh_catalog().records
        if record.root is TeamRepositoryPermission
    )


class TeamMappingRegistrationTest(TestCase):
    def test_accepted_team_mapping_is_one_record(self):
        record = _team_record()
        self.assertIs(record.root, TeamRepositoryPermission)
        self.assertIs(record.content_model, Repository)
        self.assertEqual(record.user_path, ('team', 'members'))
        self.assertEqual(record.user_field, 'team__members')
        self.assertIn(
            'condition: "$permission.id in team__allowed_operations and '
            'team__organization == repository__organization"',
            policy_sql_text(),
        )

    def test_helpers_require_a_recorder(self):
        with self.assertRaises(TypeError):
            register_team(object())
        with self.assertRaises(TypeError):
            register_collaborator(object())
        with self.assertRaises(TypeError):
            register_organization_owner(object())


class TeamCeilingAndOrganizationTest(GhFixtureMixin, TestCase):
    def test_ceiling_and_organization_must_both_hold(self):
        backend = GhAuthorizationBackend()
        read = permission_code(self.read)
        write = permission_code(self.write)
        self.assertTrue(backend.has_perm(self.member, read, self.repo_a))
        self.assertFalse(backend.has_perm(self.member, write, self.repo_a))
        TeamRepositoryPermission.objects.create(
            team=self.writers, repository=self.repo_other, operation=self.read,
        )
        self.assertFalse(backend.has_perm(self.member, read, self.repo_other))
