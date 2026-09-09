"""Acceptance matrix for the bounded GH policy (issue #1)."""

from django.test import TestCase, TransactionTestCase

from trusts.runtime import (
    authorized_q,
    filter_authorized,
    is_authorized,
    require_authorized,
)
from trusts.trustee import Trustee

from gh_permissions.models import (
    AccountRepoGrant,
    PermissionBundle,
    Repository,
    TeamRepoGrant,
)
from gh_permissions.policy import DIRECT_ADAPTER, TEAM_ADAPTER
from tests.fixtures import GhFixtureMixin


class GhProcessWideRegistrationTest(TestCase):
    def test_ready_registered_identity_and_or_adapters(self):
        from trusts.context import Context
        from trusts.path import compose

        self.assertTrue(Context.is_registered(Repository))
        path = compose(Repository, None)
        self.assertEqual(path.resource_to_scope, '')
        self.assertEqual(
            set(path.adapter_names), {TEAM_ADAPTER, DIRECT_ADAPTER},
        )
        self.assertEqual(Trustee.registry.operation_lookup(), 'code')


class GhAuthorizationTest(GhFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def _object_list_agree(self, principal, operation, obj, expected):
        with self.assertNumQueries(1):
            via_obj = is_authorized(principal, operation, obj)
        with self.assertNumQueries(1):
            via_exists = Repository.objects.filter(pk=obj.pk).filter(
                authorized_q(Repository, principal, operation),
            ).exists()
        with self.assertNumQueries(1):
            via_list = obj in list(
                filter_authorized(
                    Repository.objects.filter(pk=obj.pk), principal, operation,
                )
            )
        self.assertEqual(via_obj, expected)
        self.assertEqual(via_exists, expected)
        self.assertEqual(via_list, expected)

    def test_direct_account_can_perform_granted_operation(self):
        self._object_list_agree(
            self.collaborator, 'write', self.repo_b, True,
        )
        self._object_list_agree(
            self.collaborator, 'read', self.repo_b, False,
        )

    def test_team_member_can_perform_granted_operation(self):
        self._object_list_agree(self.member, 'read', self.repo_a, True)
        self.assertIn(self.writers, list(self.member.teams.all()))
        self.assertIn(self.bundle, list(self.writers.permission_bundles.all()))

    def test_team_membership_alone_grants_nothing(self):
        self._object_list_agree(self.member, 'read', self.repo_b, False)

    def test_org_membership_alone_grants_nothing(self):
        self._object_list_agree(self.org_only, 'read', self.repo_a, False)

    def test_team_attachment_without_bundle_operation_grants_nothing(self):
        TeamRepoGrant.objects.create(
            team=self.writers, repository=self.repo_a, operation=self.write,
        )
        self._object_list_agree(self.member, 'write', self.repo_a, False)
        self.bundle.operations.add(self.write)
        self._object_list_agree(self.member, 'write', self.repo_a, True)

    def test_other_team_or_org_does_not_leak(self):
        other_bundle = PermissionBundle.objects.create(
            team=self.outsiders, name='other-reader',
        )
        other_bundle.operations.add(self.read)
        TeamRepoGrant.objects.create(
            team=self.outsiders, repository=self.repo_other, operation=self.read,
        )
        self._object_list_agree(self.member, 'read', self.repo_other, False)
        self._object_list_agree(self.stranger, 'read', self.repo_a, False)

    def test_multiple_valid_paths_combine_by_or(self):
        AccountRepoGrant.objects.create(
            account=self.member, repository=self.repo_b, operation=self.read,
        )
        self._object_list_agree(self.member, 'read', self.repo_a, True)
        self._object_list_agree(self.member, 'read', self.repo_b, True)
        with self.assertNumQueries(1):
            listed = list(
                Repository.objects.authorized(self.member, 'read')
                .order_by('pk').values_list('pk', flat=True)
            )
        self.assertEqual(listed, [self.repo_a.pk, self.repo_b.pk])

    def test_removing_membership_or_grant_revokes_access(self):
        self.assertTrue(is_authorized(self.member, 'read', self.repo_a))
        self.writers.members.remove(self.member)
        self._object_list_agree(self.member, 'read', self.repo_a, False)
        self.writers.members.add(self.member)
        self.assertTrue(is_authorized(self.member, 'read', self.repo_a))
        TeamRepoGrant.objects.filter(
            team=self.writers, repository=self.repo_a, operation=self.read,
        ).delete()
        self._object_list_agree(self.member, 'read', self.repo_a, False)

        self.assertTrue(is_authorized(self.collaborator, 'write', self.repo_b))
        AccountRepoGrant.objects.filter(
            account=self.collaborator, repository=self.repo_b,
        ).delete()
        self._object_list_agree(self.collaborator, 'write', self.repo_b, False)

    def test_cross_org_team_grant_is_capped_by_alignment(self):
        TeamRepoGrant.objects.create(
            team=self.writers, repository=self.repo_other, operation=self.read,
        )
        self._object_list_agree(self.member, 'read', self.repo_other, False)

    def test_authorized_listing_is_sql_and_fixed_query_count(self):
        with self.assertNumQueries(1):
            listed = list(
                filter_authorized(
                    Repository.objects.all(), self.member, 'read',
                ).order_by('pk').values_list('pk', flat=True)
            )
        self.assertEqual(listed, [self.repo_a.pk])
        exists_sql = str(
            Repository.objects.filter(pk=self.repo_a.pk).filter(
                authorized_q(Repository, self.member, 'read'),
            ).query
        )
        list_sql = str(
            filter_authorized(
                Repository.objects.all(), self.member, 'read',
            ).query
        )
        combined = (exists_sql + list_sql).lower().replace('_', '').replace('"', '')
        self.assertIn('exists', combined)
        self.assertIn('teamrepogrant', combined)
        self.assertIn('permissionbundle', combined)

    def test_require_authorized_returns_or_denies(self):
        self.assertIs(
            require_authorized(self.member, 'read', self.repo_a),
            self.repo_a,
        )
        from trusts.runtime import AuthorizationDenied
        with self.assertRaises(AuthorizationDenied):
            require_authorized(self.member, 'read', self.repo_b)

    def test_string_operation_does_not_preliminary_get(self):
        with self.assertNumQueries(1):
            self.assertTrue(is_authorized(self.member, 'read', self.repo_a))
        with self.assertNumQueries(1):
            self.assertFalse(is_authorized(self.member, 'missing', self.repo_a))
