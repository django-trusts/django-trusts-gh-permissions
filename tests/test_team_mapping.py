"""Accepted team mapping is expressible through ``handle.register``.

django-trusts#54 r7 / issue #3 accepted team registration, flattened
ceiling after #12, public Django ``__`` paths after #131, the
relationship method after Core #174, and the public ``register`` plus
symbolic condition after Core #211, proven on #213::

    handle.register(
        trust=TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=lambda t: (
            t.team.allowed_operations.contains(t.operation)
            & (t.team.organization == t.repository.organization)
        ),
    )

Core stores private ``All`` / ``PermissionIn`` / ``Equal`` IR. The
application callable is not stored. Django 6.1 defines
``assertNumQueries`` on ``TransactionTestCase`` (and therefore
``TestCase``), not ``SimpleTestCase``.
"""

from django.test import TestCase

from trusts.core import (
    All,
    Equal,
    PermissionIn,
    Ref,
    TrustsRegistry,
)

from gh_permissions.models import Repository, TeamRepositoryPermission
from gh_permissions.policy import register_direct, register_team
from tests.fixtures import isolated_handle


class TeamMappingRegistrationTest(TestCase):
    def test_accepted_team_mapping_registers_with_zero_sql(self):
        handle = isolated_handle()
        with self.assertNumQueries(0):
            record = register_team(handle)
        self.assertEqual(len(handle.registry.records), 1)
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
        handle = isolated_handle()
        record = register_team(handle)
        self.assertEqual(len(handle.registry.records), 1)
        self.assertIsInstance(record.condition, All)
        kinds = [type(node) for node in record.condition.predicates]
        self.assertEqual(kinds, [PermissionIn, Equal])
        membership = record.condition.predicates[0]
        equality = record.condition.predicates[1]
        root = Ref(TeamRepositoryPermission)
        self.assertEqual(membership.refs, (root.team.allowed_operations,))
        self.assertEqual(equality.left, root.team.organization)
        self.assertEqual(equality.right, root.repository.organization)

    def test_helpers_require_a_backend_handle(self):
        registry = TrustsRegistry()
        with self.assertRaises(TypeError):
            register_team(registry)
        with self.assertRaises(TypeError):
            register_direct(registry)
