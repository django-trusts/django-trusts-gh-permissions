"""Acceptance matrix for the bounded GH policy that C2 can express."""

from django.test import TransactionTestCase

from trusts.apps import kernel_config
from trusts.core import TrustsConfigurationError

from gh_permissions.models import AccountRepoGrant, Repository
from tests.fixtures import GhFixtureMixin


def _registry():
    return kernel_config().configured_backend().registry


class GhDirectAuthorizationTest(GhFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def _object_list_agree(self, principal, operation, obj, expected):
        registry = _registry()
        with self.assertNumQueries(1):
            via_obj = registry.has_permission(principal, obj, operation)
        with self.assertNumQueries(1):
            via_exists = Repository.objects.filter(pk=obj.pk).authorized(
                principal, operation,
            ).exists()
        with self.assertNumQueries(1):
            via_list = obj in list(
                registry.filter_authorized(
                    Repository.objects.filter(pk=obj.pk), principal, operation,
                )
            )
        with self.assertNumQueries(1):
            via_manager = obj in list(
                Repository.objects.filter(pk=obj.pk).authorized(
                    principal, operation,
                )
            )
        enumerated = {
            row.pk for row in registry.permissions_for(principal, obj)
        }
        self.assertEqual(via_obj, expected)
        self.assertEqual(via_exists, expected)
        self.assertEqual(via_list, expected)
        self.assertEqual(via_manager, expected)
        if expected:
            self.assertIn(operation.pk, enumerated)
        else:
            self.assertNotIn(operation.pk, enumerated)

    def test_direct_account_can_perform_granted_operation(self):
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )
        self._object_list_agree(
            self.collaborator, self.read, self.repo_b, False,
        )

    def test_team_membership_alone_grants_nothing(self):
        self._object_list_agree(self.member, self.read, self.repo_a, False)
        self._object_list_agree(self.member, self.read, self.repo_b, False)

    def test_org_membership_alone_grants_nothing(self):
        self._object_list_agree(self.org_only, self.read, self.repo_a, False)

    def test_attachment_without_direct_grant_grants_nothing(self):
        self._object_list_agree(self.stranger, self.read, self.repo_other, False)

    def test_multiple_direct_grant_rows_combine(self):
        """Two AccountRepoGrant rows under the one direct registration.

        This is not independent-root OR (direct vs team). That remains
        unproven until ``register_team`` is expressible on public C2.
        """
        AccountRepoGrant.objects.create(
            account=self.member, repository=self.repo_a, operation=self.read,
        )
        AccountRepoGrant.objects.create(
            account=self.member, repository=self.repo_b, operation=self.read,
        )
        self._object_list_agree(self.member, self.read, self.repo_a, True)
        self._object_list_agree(self.member, self.read, self.repo_b, True)
        with self.assertNumQueries(1):
            listed = list(
                Repository.objects.authorized(self.member, self.read)
                .order_by('pk').values_list('pk', flat=True)
            )
        self.assertEqual(listed, [self.repo_a.pk, self.repo_b.pk])

    def test_removing_a_direct_grant_row_revokes_only_that_row(self):
        AccountRepoGrant.objects.create(
            account=self.member, repository=self.repo_a, operation=self.read,
        )
        AccountRepoGrant.objects.create(
            account=self.member, repository=self.repo_b, operation=self.read,
        )
        self.assertTrue(
            _registry().has_permission(self.member, self.repo_a, self.read),
        )
        AccountRepoGrant.objects.filter(
            account=self.member, repository=self.repo_a, operation=self.read,
        ).delete()
        self._object_list_agree(self.member, self.read, self.repo_a, False)
        self._object_list_agree(self.member, self.read, self.repo_b, True)

    def test_authorized_listing_is_sql_and_fixed_query_count(self):
        with self.assertNumQueries(1):
            listed = list(
                Repository.objects.authorized(
                    self.collaborator, self.write,
                ).order_by('pk').values_list('pk', flat=True)
            )
        self.assertEqual(listed, [self.repo_b.pk])
        exists_sql = str(
            Repository.objects.filter(pk=self.repo_b.pk).authorized(
                self.collaborator, self.write,
            ).query
        )
        list_sql = str(
            Repository.objects.authorized(self.collaborator, self.write).query
        )
        combined = (exists_sql + list_sql).lower().replace('_', '').replace('"', '')
        self.assertIn('exists', combined)
        self.assertIn('accountrepogrant', combined)

    def test_string_permission_is_rejected_with_zero_sql(self):
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                list(Repository.objects.authorized(self.collaborator, 'write'))
