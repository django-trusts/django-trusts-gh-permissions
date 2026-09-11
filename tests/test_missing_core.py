"""G1 missing-core stop: accepted team mapping is not expressible on C2.

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

Exact C2 ``db5a41ed66478b79e10b0a066c8c0bca8fbe7882``:

- does not export ``All``, ``Equal``, or ``permission_in`` from
  ``trusts.core``;
- rejects the membership hop (M2M after a forward single) as a user
  path;
- rejects any non-None ``condition=``.

This test must fail until those public C2 surfaces exist. Do not add a
consumer-local Q / lookup-string / tuple / callable dialect. See
``MISSING_CORE.md``.
"""

from django.test import SimpleTestCase

from trusts.core import TrustsRegistry

from gh_permissions.policy import register_team


class MissingCoreTeamMappingTest(SimpleTestCase):
    def test_accepted_team_mapping_registers_with_zero_sql(self):
        import trusts.core as core

        missing = [
            name for name in ('All', 'Equal', 'permission_in')
            if not hasattr(core, name)
        ]
        self.assertEqual(
            missing, [],
            'C2 is missing typed predicates required by the accepted GH '
            'team mapping: %s. See MISSING_CORE.md.' % (missing,),
        )
        registry = TrustsRegistry()
        with self.assertNumQueries(0):
            record = register_team(registry)
        self.assertEqual(len(registry.records), 1)
        self.assertIs(record.root.__name__, 'TeamRepoGrant')
