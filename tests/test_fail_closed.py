"""Malformed, incomplete, or undeclared paths fail closed on C2."""

from django.apps import apps
from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase

from trusts.core import TrustsConfigurationError, TrustsRegistry

from gh_permissions.models import Organization, Repository
from gh_permissions.policy import register_direct, register_gh
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
    def setUp(self):
        super(GhFailClosedTest, self).setUp()
        self.registry = TrustsRegistry()
        register_gh(self.registry)

    def test_undeclared_organization_is_empty_false_none(self):
        with self.assertNumQueries(0):
            self.assertFalse(
                self.registry.has_permission(
                    self.member, self.org_a, self.read,
                )
            )
            listed = self.registry.filter_authorized(
                Organization.objects.all(), self.member, self.read,
            )
            self.assertIsNone(listed._result_cache)
            self.assertFalse(listed.exists())
            self.assertEqual(
                list(self.registry.permissions_for(self.member, self.org_a)),
                [],
            )

    def test_wrong_requester_model_is_empty_and_raw_pk_is_configuration_error(self):
        with self.assertNumQueries(0):
            self.assertFalse(
                self.registry.has_permission(
                    self.repo_a, self.repo_a, self.read,
                )
            )
            with self.assertRaises(TrustsConfigurationError):
                self.registry.has_permission(
                    self.member.pk, self.repo_a, self.read,
                )

    def test_empty_isolated_registry_fails_closed(self):
        empty = TrustsRegistry()
        with self.assertNumQueries(0):
            self.assertFalse(
                empty.has_permission(self.member, self.repo_a, self.read)
            )
            self.assertFalse(
                empty.filter_authorized(
                    Repository.objects.all(), self.member, self.read,
                ).exists()
            )

    def test_late_registration_after_freeze_is_rejected(self):
        config = apps.get_app_config('gh_permissions')
        self.assertTrue(config.registry.frozen)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_direct(config.registry)

    def test_malformed_ref_is_rejected_without_sql(self):
        from trusts.core import Ref
        from gh_permissions.models import AccountRepoGrant

        registry = TrustsRegistry()
        d = Ref(AccountRepoGrant)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=d.repository,
                    user=d.account.name,
                    permission=d.operation,
                )
            with self.assertRaises(TrustsConfigurationError):
                registry.register(
                    content=d.repository,
                    user=d.account,
                    permission=d.operation,
                    condition='not-a-predicate',
                )

    def test_system_checks_are_clean_and_zero_sql(self):
        with self.assertNumQueries(0):
            messages = [
                m for m in run_checks()
                if getattr(m, 'id', '').startswith('trusts.E')
            ]
        self.assertEqual(messages, [])
