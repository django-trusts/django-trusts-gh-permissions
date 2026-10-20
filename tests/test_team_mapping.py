"""Accepted team mapping is expressible through ``recorder.register``.

django-trusts#54 r7 / issue #3 accepted team registration, flattened
ceiling after #12, public Django ``__`` paths after #131, the
relationship method after Core #174, and the public ``register`` plus
symbolic condition after Core #211. The catalog sweep lowers that
callable to private ``All`` / ``PermissionIn`` / ``Equal`` IR. The
application callable is not stored. Django 6.1 defines
``assertNumQueries`` on ``TransactionTestCase`` (and therefore
``TestCase``), not ``SimpleTestCase``.
"""

from django.test import TestCase

from trusts.compile.nodes import All, Equal, PermissionIn, Ref

from gh_permissions.models import Repository, TeamRepositoryPermission
from gh_permissions.policy import (
    register_collaborator,
    register_organization_owner,
    register_team,
)
from tests.fixtures import gh_catalog


def _team_record():
    return next(
        record for record in gh_catalog().records
        if record.root is TeamRepositoryPermission
    )


class TeamMappingRegistrationTest(TestCase):
    def test_accepted_team_mapping_registers_with_zero_sql(self):
        with self.assertNumQueries(0):
            record = _team_record()
        self.assertIs(record.root, TeamRepositoryPermission)
        self.assertIs(record.content_model, Repository)
        self.assertEqual(record.user_path, ('team', 'members'))
        self.assertEqual(record.user_field, 'team__members')
        self.assertFalse(callable(record.condition))
        self.assertIsInstance(record.condition, All)
        self.assertEqual(len(record.condition.predicates), 2)
        self.assertIsInstance(record.condition.predicates[0], PermissionIn)
        self.assertIsInstance(record.condition.predicates[1], Equal)

    def test_team_ceiling_and_org_equality_are_and_on_one_record(self):
        record = _team_record()
        self.assertIsInstance(record.condition, All)
        kinds = [type(node) for node in record.condition.predicates]
        self.assertEqual(kinds, [PermissionIn, Equal])
        membership = record.condition.predicates[0]
        equality = record.condition.predicates[1]
        root = Ref(TeamRepositoryPermission)
        self.assertEqual(membership.refs, (root.team.allowed_operations,))
        self.assertEqual(equality.left, root.team.organization)
        self.assertEqual(equality.right, root.repository.organization)

    def test_helpers_require_a_recorder(self):
        with self.assertRaises(TypeError):
            register_team(object())
        with self.assertRaises(TypeError):
            register_collaborator(object())
        with self.assertRaises(TypeError):
            register_organization_owner(object())
