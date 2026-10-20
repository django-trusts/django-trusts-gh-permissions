"""Malformed, incomplete, or stale registrations fail closed."""

from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase

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
    IsolatedRegistration,
    catalog_for,
    gh_catalog,
    isolated_catalog,
    permission_code,
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
    def _partial_catalog(self):
        return isolated_catalog(register_collaborator, register_team)

    def test_unregistered_resource_fails_closed(self):
        catalog = self._partial_catalog()
        self.assertFalse(
            catalog.has_perm(
                self.member, permission_code(self.read), self.org_a,
            ),
        )
        self.assertEqual(
            list(catalog.filter_authorized(
                Organization.objects.all(), self.member, self.read,
            )),
            [],
        )

    def test_wrong_requester_model_and_raw_pk_fail_closed(self):
        catalog = self._partial_catalog()
        self.assertFalse(
            catalog.has_perm(
                self.repo_a, permission_code(self.write), self.repo_b,
            ),
        )
        with self.assertRaises(TrustsConfigurationError):
            catalog.has_perm(
                self.member.pk, permission_code(self.read), self.repo_a,
            )

    def test_malformed_direct_path_rejected_with_zero_sql(self):
        registration = IsolatedRegistration()
        registration.recorder.register(
            trust=RepositoryCollaborator,
            content='repository__name',
            user='user',
            permission='permissions',
        )
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                catalog_for(registration)

    def test_extra_multi_valued_user_path_rejected_with_zero_sql(self):
        registration = IsolatedRegistration()
        registration.recorder.register(
            trust=TeamRepositoryPermission,
            content='repository',
            user='team__members__teams',
            permission='operation',
        )
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                catalog_for(registration)

    def test_non_none_untyped_condition_rejected_with_zero_sql(self):
        registration = IsolatedRegistration()
        registration.recorder.register(
            trust=TeamRepositoryPermission,
            content='repository',
            user='team__members',
            permission='operation',
            condition=object(),
        )
        with self.assertNumQueries(0):
            with self.assertRaises(TypeError):
                catalog_for(registration)

    def test_compile_freezes_further_registration(self):
        registration = IsolatedRegistration()
        registration.recorder.register(
            trust=RepositoryCollaborator,
            user='user',
            permission='permissions',
            content='repository',
        )
        with self.assertNumQueries(0):
            built = catalog_for(registration)
        self.assertEqual(len(built.records), 1)
        self.assertIs(built.records[0].root, RepositoryCollaborator)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registration.recorder.register(
                    trust=TeamRepositoryPermission,
                    user='team__members',
                    permission='operation',
                    content='repository',
                )
        self.assertEqual(len(built.records), 1)

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
        with self.assertNumQueries(0):
            record = isolated_catalog(register_team).records[0]
        self.assertIs(record.root, TeamRepositoryPermission)

    def test_duplicate_team_registration_fails_when_compiled(self):
        registration = IsolatedRegistration()
        register_team(registration.recorder)
        register_team(registration.recorder)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                catalog_for(registration)

    def test_register_team_adds_independent_root_beside_collaborator(self):
        with self.assertNumQueries(0):
            catalog = isolated_catalog(
                register_collaborator,
                register_team,
                register_organization_owner,
            )
        self.assertEqual(
            [record.root for record in catalog.records],
            [
                RepositoryCollaborator,
                TeamRepositoryPermission,
                OrganizationOwnership,
                OrganizationOwnership,
            ],
        )
        self.assertIsNone(catalog.records[-1].condition)
        registration = IsolatedRegistration()
        register_organization_owner(registration.recorder)
        register_organization_owner(registration.recorder)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                catalog_for(registration)

    def test_startup_registers_direct_and_team_roots(self):
        roots = [record.root for record in gh_catalog().records]
        self.assertEqual(roots, [
            RepositoryCollaborator,
            TeamRepositoryPermission,
            OrganizationOwnership,
            OrganizationOwnership,
            IssuePermissions,
        ])
