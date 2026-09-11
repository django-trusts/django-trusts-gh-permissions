"""Malformed, incomplete, or stale registrations fail closed."""

from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase

from trusts.core import Ref, TrustsConfigurationError, TrustsRegistry

from gh_permissions.models import (
    AccountRepoGrant,
    Organization,
    TeamRepoGrant,
)
from gh_permissions.policy import register_direct
from tests.fixtures import GhFixtureMixin


# Intentionally unsupported GH behaviors (not defects of this consumer):
UNSUPPORTED_GH_BEHAVIORS = (
    'permission levels do not imply lower levels unless the bundle lists them',
    'no org-owner implicit admin on every repository',
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
        registry = TrustsRegistry()
        register_direct(registry)
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
        registry = TrustsRegistry()
        register_direct(registry)
        self.assertFalse(
            registry.has_permission(self.repo_a, self.repo_b, self.write),
        )
        with self.assertRaises(TrustsConfigurationError):
            registry.has_permission(self.member.pk, self.repo_a, self.read)

    def test_malformed_direct_path_rejected_with_zero_sql(self):
        registry = TrustsRegistry()
        d = Ref(AccountRepoGrant)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=d.repository.title,
                    user=d.account,
                    permission=d.operation,
                )
        self.assertEqual(registry.records, ())

    def test_m2m_user_path_rejected_with_zero_sql(self):
        registry = TrustsRegistry()
        t = Ref(TeamRepoGrant)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=t.repository,
                    user=t.team.members,
                    permission=t.operation,
                )
        self.assertEqual(registry.records, ())

    def test_non_none_untyped_condition_rejected_with_zero_sql(self):
        registry = TrustsRegistry()
        t = Ref(TeamRepoGrant)
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
        from trusts.apps import kernel_config

        registry = kernel_config().configured_backend().registry
        self.assertTrue(registry.frozen)
        d = Ref(AccountRepoGrant)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_direct(registry)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=d.repository,
                    user=d.account,
                    permission=d.operation,
                )

    def test_system_checks_are_clean_for_complete_gh_install(self):
        with self.assertNumQueries(0):
            messages = [
                m for m in run_checks()
                if getattr(m, 'id', '').startswith('trusts.E')
            ]
        self.assertEqual(messages, [])
