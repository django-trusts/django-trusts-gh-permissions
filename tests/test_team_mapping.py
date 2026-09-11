"""Accepted team mapping is expressible on public C2 after #98.

django-trusts#54 r7 / issue #3 accepted team registration::

    t = Ref(TeamRepoGrant)
    registry.register(
        content=t.repository,
        user=t.team.members,
        permission=t.operation,
        condition=All(
            permission_in(t.team.permission_bundles.operations),
            Equal(t.team.organization, t.repository.organization),
        ),
    )

Exact kernel ``39f1f9611e214193aec4e97526cf9b54ee689967`` (Step I)
exports ``All``, ``Equal``, and ``permission_in`` from ``trusts.core``
and accepts the terminal M2M membership hop. Django 6.1 defines
``assertNumQueries`` on ``TransactionTestCase`` (and therefore
``TestCase``), not ``SimpleTestCase``.
"""

from django.test import TestCase

from trusts.core import All, Equal, PermissionIn, TrustsRegistry, permission_in

from gh_permissions.models import Repository, TeamRepoGrant
from gh_permissions.policy import register_team


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
        registry = TrustsRegistry()
        with self.assertNumQueries(0):
            record = register_team(registry)
        self.assertEqual(len(registry.records), 1)
        self.assertIs(record.root, TeamRepoGrant)
        self.assertIs(record.content_model, Repository)
        self.assertEqual(record.user_path, ('team', 'members'))
        self.assertEqual(record.user_field, 'team__members')
        self.assertIsInstance(record.condition, All)
        self.assertEqual(len(record.condition.predicates), 2)
        self.assertIsInstance(record.condition.predicates[0], PermissionIn)
        self.assertIsInstance(record.condition.predicates[1], Equal)
