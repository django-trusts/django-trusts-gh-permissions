"""Malformed, incomplete, or stale registrations fail closed."""

from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase

from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationOwnership,
    RepositoryCollaborator,
    IssuePermissions,
    Team,
    TeamRepositoryPermission,
)
from tests.fixtures import GhFixtureMixin, gh_trusts, permission_code


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
        backend = GhAuthorizationBackend()
        team = Team.objects.create(organization=self.org_a, name='unplanned')
        alias = Alias.objects.create(name='unplanned-alias')
        code = permission_code(self.read)
        self.assertFalse(backend.has_perm(self.member, code, team))
        self.assertFalse(backend.has_perm(self.member, code, alias))

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
        self.assertFalse(
            backend.has_perm(
                self.member.pk, permission_code(self.read), self.repo_a,
            ),
        )

    def test_system_checks_are_clean_for_complete_gh_install(self):
        with self.assertNumQueries(0):
            messages = [
                m for m in run_checks()
                if getattr(m, 'id', '').startswith('trusts.E')
            ]
        self.assertEqual(messages, [])

    def test_startup_registers_direct_and_team_roots(self):
        trusts = gh_trusts()
        self.assertEqual([trust['root'] for _model, trust in trusts], [
            RepositoryCollaborator._meta.label,
            TeamRepositoryPermission._meta.label,
            OrganizationOwnership._meta.label,
            OrganizationOwnership._meta.label,
            IssuePermissions._meta.label,
        ])
        self.assertEqual(
            trusts[1][1]['root'], TeamRepositoryPermission._meta.label,
        )
        ownerships = [
            trust for _model, trust in trusts
            if trust['root'] == OrganizationOwnership._meta.label
        ]
        self.assertEqual(len(ownerships), 2)
        for trust in ownerships:
            self.assertNotIn('condition', trust)
