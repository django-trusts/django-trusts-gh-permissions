"""Malformed, incomplete, or stale registrations fail closed."""

import django.apps
from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase

from trusts.compile.nodes import Ref
from trusts.errors import TrustsConfigurationError

from gh_permissions import policy as gh_policy
from gh_permissions.models import (
    Organization,
    OrganizationOwnership,
    RepositoryCollaborator,
    IssuePermissions,
    TeamRepositoryPermission,
)
from gh_permissions.policy import (
    register_collaborator,
    register_organization_owner,
    register_team,
)
from tests.fixtures import (
    GhFixtureMixin,
    compile_recorder,
    isolated_compiler,
    isolated_recorder,
)


# Intentionally unsupported GH behaviors (not defects of this consumer):
UNSUPPORTED_GH_BEHAVIORS = (
    'permission levels do not imply lower levels unless the team lists them',
    'no owner grant on repositories outside that organization',
    'no public-repository anonymous read',
    'no nested teams',
    'no outside-collaborator invitation workflow',
    'no fine-grained token or app-installation scopes',
    'no branch protection, deploy keys, or Actions secrets',
    'no fork, CODEOWNERS, or visibility matrix',
    'no org default repository permission',
)


class UnsupportedGhBehaviorTest(SimpleTestCase):
    def test_unsupported_behaviors_are_stated(self):
        self.assertGreaterEqual(len(UNSUPPORTED_GH_BEHAVIORS), 6)
        joined = ' '.join(UNSUPPORTED_GH_BEHAVIORS)
        self.assertNotIn('GitHub', joined)


class GhFailClosedTest(GhFixtureMixin, TestCase):
    def test_unregistered_resource_fails_closed(self):
        recorder = isolated_recorder()
        register_collaborator(recorder)
        register_team(recorder)
        catalog = compile_recorder(recorder)
        self.assertFalse(
            catalog.has_permission(self.member, self.org_a, self.read),
        )
        self.assertEqual(
            list(catalog.filter_authorized(
                Organization.objects.all(), self.member, self.read,
            )),
            [],
        )

    def test_wrong_requester_model_and_raw_pk_fail_closed(self):
        recorder = isolated_recorder()
        register_collaborator(recorder)
        register_team(recorder)
        catalog = compile_recorder(recorder)
        self.assertFalse(
            catalog.has_permission(self.repo_a, self.repo_b, self.write),
        )
        with self.assertRaises(TrustsConfigurationError):
            catalog.has_permission(self.member.pk, self.repo_a, self.read)

    def test_malformed_direct_path_rejected_with_zero_sql(self):
        compiler = isolated_compiler()
        d = Ref(RepositoryCollaborator)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                compiler.register_relation(
                    content=d.repository.name,
                    user=d.user,
                    permission=d.permissions,
                )
        self.assertEqual(compiler.records, ())

    def test_extra_multi_valued_user_path_rejected_with_zero_sql(self):
        compiler = isolated_compiler()
        t = Ref(TeamRepositoryPermission)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                compiler.register_relation(
                    content=t.repository,
                    user=t.team.members.teams,
                    permission=t.operation,
                )
        self.assertEqual(compiler.records, ())

    def test_non_none_untyped_condition_rejected_with_zero_sql(self):
        compiler = isolated_compiler()
        t = Ref(TeamRepositoryPermission)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                compiler.register_relation(
                    content=t.repository,
                    user=t.team,
                    permission=t.operation,
                    condition=object(),
                )
        self.assertEqual(compiler.records, ())

    def test_late_registration_after_freeze_is_rejected(self):
        recorder = django.apps.apps.get_app_config('gh_permissions').recorder
        self.assertTrue(recorder.frozen)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_collaborator(recorder)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_team(recorder)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_organization_owner(recorder)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                recorder.register(
                    trust=RepositoryCollaborator,
                    content='repository',
                    user='user',
                    permission='permissions',
                )

    def test_system_checks_are_clean_for_complete_gh_install(self):
        with self.assertNumQueries(0):
            messages = [
                m for m in run_checks()
                if getattr(m, 'id', '').startswith('trusts.E')
            ]
        self.assertEqual(messages, [])

    def test_no_aggregate_register_gh_policy_helper(self):
        self.assertFalse(hasattr(gh_policy, 'register_gh_policy'))

    def test_register_team_adds_one_record_with_zero_sql(self):
        recorder = isolated_recorder()
        with self.assertNumQueries(0):
            register_team(recorder)
            catalog = compile_recorder(recorder)
        self.assertEqual(len(catalog.records), 1)
        self.assertIs(catalog.records[0].root, TeamRepositoryPermission)

    def test_duplicate_team_registration_fails_closed_without_mutation(self):
        recorder = isolated_recorder()
        register_team(recorder)
        catalog = compile_recorder(recorder)
        before = catalog.records
        duplicate = isolated_recorder()
        register_team(duplicate)
        register_team(duplicate)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                compile_recorder(duplicate)
        self.assertEqual(catalog.records, before)
        self.assertEqual(len(before), 1)

    def test_register_team_adds_independent_root_beside_collaborator(self):
        recorder = isolated_recorder()
        register_collaborator(recorder)
        register_team(recorder)
        with self.assertNumQueries(0):
            register_organization_owner(recorder)
            catalog = compile_recorder(recorder)
        self.assertEqual(len(catalog.records), 4)
        self.assertIs(catalog.records[0].root, RepositoryCollaborator)
        self.assertIs(catalog.records[1].root, TeamRepositoryPermission)
        self.assertIs(catalog.records[2].root, OrganizationOwnership)
        self.assertIsNone(catalog.records[2].condition)
        duplicate = isolated_recorder()
        register_collaborator(duplicate)
        register_team(duplicate)
        register_organization_owner(duplicate)
        register_organization_owner(duplicate)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                compile_recorder(duplicate)

    def test_startup_registers_direct_and_team_roots(self):
        roots = [
            record.root
            for record in django.apps.apps.get_app_config(
                'gh_permissions',
            ).get_catalog().records
        ]
        self.assertEqual(roots, [
            RepositoryCollaborator,
            TeamRepositoryPermission,
            OrganizationOwnership,
            OrganizationOwnership,
            IssuePermissions,
        ])
