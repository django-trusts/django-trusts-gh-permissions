"""Accepted team mapping is expressible through ``handle.register_relationship``.

django-trusts#54 r7 / issue #3 accepted team registration, flattened
ceiling after #12, public Django ``__`` paths after #131, and the
public relationship method after Core #174::

    handle.register_relationship(
        TeamRepositoryPermission,
        user='team__members',
        permission='operation',
        content='repository',
        condition=All(
            permission_in('team__allowed_operations'),
            Equal('team__organization', 'repository__organization'),
        ),
    )

Core exports ``All``, ``Equal``, and ``permission_in`` from
``trusts.core`` and accepts the terminal M2M membership hop. Django 6.1
defines ``assertNumQueries`` on ``TransactionTestCase`` (and therefore
``TestCase``), not ``SimpleTestCase``.
"""

from django.test import TestCase

from trusts.core import (
    All,
    Equal,
    PermissionIn,
    TrustsRegistry,
    permission_in,
)

from gh_permissions.models import Repository, TeamRepositoryPermission
from gh_permissions.policy import register_direct, register_team
from tests.fixtures import isolated_handle


class TeamMappingRegistrationTest(TestCase):
    def test_core_exports_closed_predicate_nodes(self):
        import trusts.core as core

        missing = [
            name for name in ('All', 'Equal', 'permission_in')
            if not hasattr(core, name)
        ]
        self.assertEqual(missing, [])
        self.assertIs(permission_in, PermissionIn)

    def test_accepted_team_mapping_registers_with_zero_sql(self):
        handle = isolated_handle()
        with self.assertNumQueries(0):
            record = register_team(handle)
        self.assertEqual(len(handle.registry.records), 1)
        self.assertIs(record.root, TeamRepositoryPermission)
        self.assertIs(record.content_model, Repository)
        self.assertEqual(record.user_path, ('team', 'members'))
        self.assertEqual(record.user_field, 'team__members')
        self.assertIsInstance(record.condition, All)
        self.assertEqual(len(record.condition.predicates), 2)
        self.assertIsInstance(record.condition.predicates[0], PermissionIn)
        self.assertIsInstance(record.condition.predicates[1], Equal)

    def test_helpers_require_a_backend_handle(self):
        registry = TrustsRegistry()
        with self.assertRaises(TypeError):
            register_team(registry)
        with self.assertRaises(TypeError):
            register_direct(registry)
