"""Malformed, incomplete, or stale registrations fail closed."""

from django.core.checks import run_checks
from django.test import SimpleTestCase, TestCase

from trusts.context import Context, ContextRegistry
from trusts.path import AuthorizationPathError, compose
from trusts.runtime import AuthorizationConfigError, filter_authorized, is_authorized
from trusts.trustee import (
    Trustee,
    TrusteeRegistrationError,
    TrusteeRegistry,
)

from gh_permissions.models import (
    Account,
    Operation,
    Organization,
    Repository,
    Team,
    TeamRepoGrant,
)
from gh_permissions.policy import TEAM_ADAPTER, register_gh_policy
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
    def test_unregistered_resource_is_config_error(self):
        with self.assertRaises(AuthorizationConfigError):
            is_authorized(self.member, 'read', self.org_a)
        with self.assertRaises(AuthorizationConfigError):
            filter_authorized(
                Organization.objects.all(), self.member, 'read',
            )

    def test_wrong_requester_model_and_raw_pk_are_config_errors(self):
        with self.assertRaises(AuthorizationConfigError):
            is_authorized(self.repo_a, 'read', self.repo_a)
        with self.assertRaises(AuthorizationConfigError):
            is_authorized(self.member.pk, 'read', self.repo_a)

    def test_incomplete_isolated_registry_fails_closed(self):
        context = ContextRegistry()
        context.register_identity(Repository)
        trustee = TrusteeRegistry(
            requester_model=Account,
            scope_model=Repository,
            operation_model=Operation,
            operation_lookup='code',
        )
        with self.assertRaises(AuthorizationConfigError):
            is_authorized(
                self.member, 'read', self.repo_a,
                context=context, trustee=trustee,
            )

    def test_wrong_membership_terminal_fails_at_registration(self):
        trustee = TrusteeRegistry(
            requester_model=Account,
            scope_model=Repository,
            operation_model=Operation,
        )
        with self.assertRaises(TrusteeRegistrationError) as ctx:
            trustee.register(
                name=TEAM_ADAPTER,
                trustee_model=Team,
                grant_model=TeamRepoGrant,
                trustee_path='team',
                scope_path='repository',
                operation_path='operation',
                membership_path='organization',
            )
        self.assertIn('not the configured requester', str(ctx.exception))

    def test_stale_alignment_revalidate_fails_closed(self):
        from trusts.trustee import TrusteeAdapter

        context = ContextRegistry()
        trustee = TrusteeRegistry()
        register_gh_policy(context=context, trustee=trustee)
        saved = trustee.get(TEAM_ADAPTER)
        trustee._adapters[TEAM_ADAPTER] = TrusteeAdapter(
            saved.kind, saved.name, saved.trustee_model, saved.membership_path,
            saved.grant_model, saved.trustee_path, saved.scope_path,
            saved.operation_path, saved.constraint_paths, trustee,
            alignment_paths=(('not_a_field', 'repository__organization'),),
        )
        try:
            with self.assertRaises(TrusteeRegistrationError):
                trustee.revalidate(trustee.get(TEAM_ADAPTER))
        finally:
            trustee._adapters[TEAM_ADAPTER] = saved

    def test_late_registration_after_freeze_is_rejected(self):
        compose(Repository, None)
        self.assertTrue(Context.is_frozen())
        self.assertTrue(Trustee.is_frozen())
        with self.assertRaises(Exception):
            Context.register_identity(Organization)
        self.assertFalse(Context.is_registered(Organization))

    def test_system_checks_are_clean_for_complete_gh_install(self):
        messages = [
            m for m in run_checks()
            if getattr(m, 'id', '').startswith('trusts.E')
        ]
        self.assertEqual(messages, [])
