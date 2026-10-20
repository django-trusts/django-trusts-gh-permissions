"""Malformed, incomplete, or stale registrations fail closed."""

from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase

from trusts.errors import TrustsConfigurationError

from gh_permissions import policy as gh_policy
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import (
    Organization,
    OrganizationOwnership,
    RepositoryCollaborator,
    IssuePermissions,
    TeamRepositoryPermission,
)
from tests.fixtures import GhFixtureMixin, gh_catalog, permission_code


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
    def test_repository_read_does_not_grant_the_organization(self):
        backend = GhAuthorizationBackend()
        self.assertFalse(
            backend.has_perm(
                self.member, permission_code(self.read), self.org_a,
            ),
        )
        self.assertEqual(
            list(Organization.objects.authorized(self.member, self.read)),
            [],
        )

    def test_wrong_requester_model_and_raw_pk_fail_closed(self):
        backend = GhAuthorizationBackend()
        self.assertFalse(
            backend.has_perm(
                self.repo_a, permission_code(self.write), self.repo_b,
            ),
        )
        with self.assertRaises(TrustsConfigurationError):
            backend.has_perm(
                self.member.pk, permission_code(self.read), self.repo_a,
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

    def test_startup_registers_direct_and_team_roots(self):
        with self.assertNumQueries(0):
            records = gh_catalog().records
        self.assertEqual([record.root for record in records], [
            RepositoryCollaborator,
            TeamRepositoryPermission,
            OrganizationOwnership,
            OrganizationOwnership,
            IssuePermissions,
        ])
        self.assertIs(records[1].root, TeamRepositoryPermission)
        self.assertIsNone(records[3].condition)
