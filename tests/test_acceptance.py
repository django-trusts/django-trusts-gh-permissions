"""Acceptance matrix for the bounded GH policy on public C2 + #98."""

from unittest.mock import patch

from django.db.models.query import QuerySet
from django.test import TransactionTestCase

from trusts.apps import configured_implementation_handles, implementation_for_path
from trusts.core import TrustsConfigurationError
from trusts.query import AuthorizedManager, AuthorizedQuerySet

from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import (
    Repository,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)
from tests.fixtures import GhFixtureMixin


def _registry():
    return implementation_for_path(CANONICAL_BACKEND).configured_backend(
        CANONICAL_BACKEND,
    ).registry


class GhAuthorizationTest(GhFixtureMixin, TransactionTestCase):
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

    def test_direct_user_can_perform_granted_operation(self):
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )
        self._object_list_agree(
            self.collaborator, self.read, self.repo_b, False,
        )

    def test_team_member_permission_ceiling_and_same_org_allows(self):
        self._object_list_agree(self.member, self.read, self.repo_a, True)
        self._object_list_agree(self.member, self.write, self.repo_a, False)
        self._object_list_agree(self.member, self.read, self.repo_b, False)

    def test_team_membership_without_grant_grants_nothing(self):
        self.outsiders.members.add(self.stranger)
        self._object_list_agree(self.stranger, self.read, self.repo_other, False)
        self._object_list_agree(self.member, self.read, self.repo_b, False)

    def test_user_without_membership_or_direct_grant_gets_nothing(self):
        self._object_list_agree(self.unattached, self.read, self.repo_a, False)

    def test_attachment_without_grant_grants_nothing(self):
        self._object_list_agree(self.stranger, self.read, self.repo_other, False)

    def test_cross_organization_team_permission_denies(self):
        TeamRepositoryPermission.objects.create(
            team=self.writers, repository=self.repo_other, operation=self.read,
        )
        self._object_list_agree(self.member, self.read, self.repo_other, False)

    def test_removing_membership_denies_only_that_branch(self):
        self.writers.members.remove(self.member)
        self._object_list_agree(self.member, self.read, self.repo_a, False)
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )

    def test_removing_team_permission_denies_only_that_branch(self):
        TeamRepositoryPermission.objects.filter(
            team=self.writers, repository=self.repo_a, operation=self.read,
        ).delete()
        self._object_list_agree(self.member, self.read, self.repo_a, False)
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )

    def test_removing_allowed_operation_denies_only_that_branch(self):
        # Local team permission row stays; only the ceiling is removed.
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(
                team=self.writers, repository=self.repo_a, operation=self.read,
            ).exists()
        )
        self.writers.allowed_operations.remove(self.read)
        self._object_list_agree(self.member, self.read, self.repo_a, False)
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )

    def test_removing_organization_alignment_denies_only_that_branch(self):
        # Local team permission row stays; only organization equality fails.
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(
                team=self.writers, repository=self.repo_a, operation=self.read,
            ).exists()
        )
        self.writers.organization = self.org_b
        self.writers.save()
        self._object_list_agree(self.member, self.read, self.repo_a, False)
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )

    def test_direct_and_team_roots_or_compose(self):
        UserRepositoryPermission.objects.create(
            user=self.member, repository=self.repo_b, operation=self.read,
        )
        self._object_list_agree(self.member, self.read, self.repo_a, True)
        self._object_list_agree(self.member, self.read, self.repo_b, True)
        with self.assertNumQueries(1):
            listed = list(
                Repository.objects.authorized(self.member, self.read)
                .order_by('pk').values_list('pk', flat=True)
            )
        self.assertEqual(listed, [self.repo_a.pk, self.repo_b.pk])
        TeamRepositoryPermission.objects.filter(
            team=self.writers, repository=self.repo_a, operation=self.read,
        ).delete()
        self._object_list_agree(self.member, self.read, self.repo_a, False)
        self._object_list_agree(self.member, self.read, self.repo_b, True)

    def test_multiple_direct_permission_rows_combine(self):
        """Two UserRepositoryPermission rows under the one direct registration."""
        UserRepositoryPermission.objects.create(
            user=self.collaborator, repository=self.repo_a,
            operation=self.write,
        )
        self._object_list_agree(
            self.collaborator, self.write, self.repo_a, True,
        )
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )
        with self.assertNumQueries(1):
            listed = list(
                Repository.objects.authorized(self.collaborator, self.write)
                .order_by('pk').values_list('pk', flat=True)
            )
        self.assertEqual(listed, [self.repo_a.pk, self.repo_b.pk])

    def test_removing_a_direct_permission_row_revokes_only_that_row(self):
        UserRepositoryPermission.objects.create(
            user=self.collaborator, repository=self.repo_a,
            operation=self.write,
        )
        self._object_list_agree(
            self.collaborator, self.write, self.repo_a, True,
        )
        UserRepositoryPermission.objects.filter(
            user=self.collaborator, repository=self.repo_a,
            operation=self.write,
        ).delete()
        self._object_list_agree(
            self.collaborator, self.write, self.repo_a, False,
        )
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )

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
        self.assertIn('userrepositorypermission', combined)

    def test_team_listing_is_sql_and_fixed_query_count(self):
        with self.assertNumQueries(1):
            listed = list(
                Repository.objects.authorized(
                    self.member, self.read,
                ).order_by('pk').values_list('pk', flat=True)
            )
        self.assertEqual(listed, [self.repo_a.pk])
        exists_sql = str(
            Repository.objects.filter(pk=self.repo_a.pk).authorized(
                self.member, self.read,
            ).query
        )
        list_sql = str(
            Repository.objects.authorized(self.member, self.read).query
        )
        combined = (exists_sql + list_sql).lower().replace('_', '').replace('"', '')
        self.assertIn('exists', combined)
        self.assertIn('teamrepositorypermission', combined)

    def test_query_construction_is_lazy_and_filters_before_pagination(self):
        registry = _registry()
        with self.assertNumQueries(0):
            qs = registry.filter_authorized(
                Repository.objects.all(), self.member, self.read,
            )
            enumerated = registry.permissions_for(self.member, self.repo_a)
        self.assertIsInstance(qs, QuerySet)
        self.assertIsInstance(enumerated, QuerySet)
        self.assertIsNone(qs._result_cache)
        with self.assertNumQueries(1):
            self.assertEqual(
                list(qs.order_by('pk').values_list('pk', flat=True)),
                [self.repo_a.pk],
            )
        page = registry.filter_authorized(
            Repository.objects.order_by('pk'), self.member, self.read,
        )[:1]
        with self.assertNumQueries(1):
            self.assertEqual(list(page), [self.repo_a])

    def test_string_permission_is_rejected_with_zero_sql(self):
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                list(Repository.objects.authorized(self.collaborator, 'write'))


class StockAuthorizedManagerTest(GhFixtureMixin, TransactionTestCase):
    """Stock core manager reaches this implementation's registered handles."""

    reset_sequences = True

    def test_stock_manager_reads_configured_implementation_handles(self):
        self.assertIs(type(Repository.objects), AuthorizedManager)
        self.assertIs(Repository.objects._queryset_class, AuthorizedQuerySet)

        handles = configured_implementation_handles()
        self.assertEqual(len(handles), 1)
        self.assertEqual(handles[0].path, CANONICAL_BACKEND)
        roots = [record.root for record in handles[0].registry.records]
        self.assertEqual(
            roots,
            [UserRepositoryPermission, TeamRepositoryPermission],
        )

        with patch(
            'trusts.apps.configured_implementation_handles', return_value=(),
        ) as mocked:
            hidden = list(Repository.objects.authorized(self.member, self.read))
        mocked.assert_called()
        self.assertEqual(hidden, [])

        listed = list(
            Repository.objects.authorized(self.member, self.read).order_by('pk')
        )
        self.assertEqual(listed, [self.repo_a])
        listed_direct = list(
            Repository.objects.authorized(
                self.collaborator, self.write,
            ).order_by('pk')
        )
        self.assertEqual(listed_direct, [self.repo_b])
