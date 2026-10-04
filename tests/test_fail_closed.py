"""Malformed, incomplete, or stale registrations fail closed."""

from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase

from trusts.core import Ref, TrustsConfigurationError, TrustsRegistry

from gh_permissions import policy as gh_policy
from gh_permissions.models import (
    Organization,
    OrganizationOwnership,
    RepositoryCollaborator,
    TeamRepositoryPermission,
)
from gh_permissions.policy import (
    register_collaborator,
    register_organization_owner,
    register_team,
)
from tests.fixtures import GhFixtureMixin, isolated_handle


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
        handle = isolated_handle()
        register_collaborator(handle)
        register_team(handle)
        registry = handle.registry
        self.assertFalse(
            registry.has_permission(self.member, self.org_a, self.read),
        )
        self.assertEqual(
            list(registry.filter_authorized(
                Organization.objects.all(), self.member, self.read,
            )),
            [],
        )

    def test_wrong_requester_model_and_raw_pk_fail_closed(self):
        handle = isolated_handle()
        register_collaborator(handle)
        register_team(handle)
        registry = handle.registry
        self.assertFalse(
            registry.has_permission(self.repo_a, self.repo_b, self.write),
        )
        with self.assertRaises(TrustsConfigurationError):
            registry.has_permission(self.member.pk, self.repo_a, self.read)

    def test_malformed_direct_path_rejected_with_zero_sql(self):
        registry = TrustsRegistry()
        d = Ref(RepositoryCollaborator)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=d.repository.name,
                    user=d.user,
                    permission=d.permissions,
                )
        self.assertEqual(registry.records, ())

    def test_extra_multi_valued_user_path_rejected_with_zero_sql(self):
        registry = TrustsRegistry()
        t = Ref(TeamRepositoryPermission)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=t.repository,
                    user=t.team.members.teams,
                    permission=t.operation,
                )
        self.assertEqual(registry.records, ())

    def test_non_none_untyped_condition_rejected_with_zero_sql(self):
        registry = TrustsRegistry()
        t = Ref(TeamRepositoryPermission)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=t.repository,
                    user=t.team,
                    permission=t.operation,
                    condition=object(),
                )
        self.assertEqual(registry.records, ())

    def test_late_registration_after_freeze_is_rejected(self):
        from trusts.apps import implementation_for_path

        from gh_permissions.apps import CANONICAL_BACKEND

        handle = implementation_for_path(CANONICAL_BACKEND).configured_backend(
            CANONICAL_BACKEND,
        )
        registry = handle.registry
        self.assertTrue(registry.frozen)
        d = Ref(RepositoryCollaborator)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_collaborator(handle)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_team(handle)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_organization_owner(handle)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=d.repository,
                    user=d.user,
                    permission=d.permissions,
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
        handle = isolated_handle()
        with self.assertNumQueries(0):
            record = register_team(handle)
        self.assertEqual(len(handle.registry.records), 1)
        self.assertIs(record.root, TeamRepositoryPermission)

    def test_duplicate_team_registration_fails_closed_without_mutation(self):
        handle = isolated_handle()
        register_team(handle)
        before = handle.registry.records
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_team(handle)
        self.assertEqual(handle.registry.records, before)

    def test_register_team_adds_independent_root_beside_collaborator(self):
        handle = isolated_handle()
        register_collaborator(handle)
        before = handle.registry.records
        self.assertEqual(len(before), 1)
        self.assertEqual(before[0].root, RepositoryCollaborator)
        with self.assertNumQueries(0):
            record = register_team(handle)
        self.assertEqual(len(handle.registry.records), 2)
        self.assertIs(handle.registry.records[0].root, RepositoryCollaborator)
        self.assertIs(record.root, TeamRepositoryPermission)
        self.assertIs(handle.registry.records[1].root, TeamRepositoryPermission)
        with self.assertNumQueries(0):
            owner_record = register_organization_owner(handle)
        self.assertEqual(len(handle.registry.records), 4)
        self.assertIs(handle.registry.records[0].root, RepositoryCollaborator)
        self.assertIs(owner_record.root, OrganizationOwnership)
        self.assertIsNone(owner_record.condition)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_organization_owner(handle)
        self.assertEqual(len(handle.registry.records), 4)

    def test_startup_registers_direct_and_team_roots(self):
        from trusts.apps import implementation_for_path

        from gh_permissions.apps import CANONICAL_BACKEND

        registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
            CANONICAL_BACKEND,
        ).registry
        roots = [record.root for record in registry.records]
        self.assertEqual(roots, [
            RepositoryCollaborator,
            TeamRepositoryPermission,
            OrganizationOwnership,
            OrganizationOwnership,
        ])
