"""Direct-account grants on the C2 public atom.

Team grants are out of scope until core ships the accepted predicates.
These tests use an isolated ``TrustsRegistry`` because GH does not list
``TrustModelBackend``.
"""

from django.apps import apps
from django.test import TestCase, TransactionTestCase

from trusts.core import TrustsConfigurationError, TrustsRegistry

from gh_permissions.models import AccountRepoGrant, Repository
from gh_permissions.policy import register_direct, register_gh
from tests.fixtures import GhFixtureMixin


def _registry():
    registry = TrustsRegistry()
    register_gh(registry)
    return registry


class DirectRegistrationTest(TestCase):
    def test_direct_register_is_zero_sql_and_returns_record(self):
        registry = TrustsRegistry()
        with self.assertNumQueries(0):
            record = register_direct(registry)
        self.assertIs(record.root, AccountRepoGrant)
        self.assertIs(record.content_model, Repository)
        self.assertEqual(record.content_path, ('repository',))
        self.assertEqual(record.user_path, ('account',))
        self.assertEqual(record.permission_path, ('operation',))
        self.assertIsNone(record.condition)

    def test_process_ready_registry_has_only_the_direct_root(self):
        config = apps.get_app_config('gh_permissions')
        registry = config.registry
        self.assertTrue(registry.frozen)
        records = registry.records_for_root(AccountRepoGrant)
        self.assertEqual(len(records), 1)
        with self.assertRaises(TrustsConfigurationError):
            registry.records_for_root(
                apps.get_model('gh_permissions', 'TeamRepoGrant'),
            )

    def test_duplicate_direct_register_is_rejected_without_sql(self):
        registry = TrustsRegistry()
        register_direct(registry)
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_direct(registry)


class DirectAuthorizationTest(GhFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super(DirectAuthorizationTest, self).setUp()
        self.registry = _registry()

    def _object_list_agree(self, principal, operation, obj, expected):
        with self.assertNumQueries(1):
            via_obj = self.registry.has_permission(principal, obj, operation)
        with self.assertNumQueries(1):
            via_list = obj in list(
                self.registry.filter_authorized(
                    Repository.objects.filter(pk=obj.pk), principal, operation,
                )
            )
        with self.assertNumQueries(1):
            via_enum = operation in list(
                self.registry.permissions_for(principal, obj)
            )
        self.assertEqual(via_obj, expected)
        self.assertEqual(via_list, expected)
        self.assertEqual(via_enum, expected)

    def test_direct_account_can_perform_granted_operation(self):
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )
        self._object_list_agree(
            self.collaborator, self.read, self.repo_b, False,
        )

    def test_membership_and_attachment_alone_grant_nothing(self):
        self._object_list_agree(self.member, self.read, self.repo_a, False)
        self._object_list_agree(self.org_only, self.read, self.repo_a, False)
        self._object_list_agree(self.stranger, self.read, self.repo_a, False)

    def test_removing_direct_grant_revokes_access(self):
        self.assertTrue(
            self.registry.has_permission(
                self.collaborator, self.repo_b, self.write,
            )
        )
        AccountRepoGrant.objects.filter(
            account=self.collaborator, repository=self.repo_b,
        ).delete()
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, False,
        )

    def test_two_direct_grants_or_compose_and_removing_one_keeps_the_other(self):
        AccountRepoGrant.objects.create(
            account=self.collaborator, repository=self.repo_a,
            operation=self.write,
        )
        self._object_list_agree(
            self.collaborator, self.write, self.repo_a, True,
        )
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )
        AccountRepoGrant.objects.filter(
            account=self.collaborator, repository=self.repo_a,
        ).delete()
        self._object_list_agree(
            self.collaborator, self.write, self.repo_a, False,
        )
        self._object_list_agree(
            self.collaborator, self.write, self.repo_b, True,
        )

    def test_authorized_listing_is_sql_filtered_before_evaluation(self):
        qs = self.registry.filter_authorized(
            Repository.objects.all(), self.collaborator, self.write,
        )
        self.assertIsNone(qs._result_cache)
        sql = str(qs.query).lower()
        self.assertIn('exists', sql)
        self.assertIn('accountrepogrant', sql.replace('_', ''))
        with self.assertNumQueries(1):
            listed = list(qs.order_by('pk').values_list('pk', flat=True))
        self.assertEqual(listed, [self.repo_b.pk])

    def test_string_permission_is_configuration_error_without_sql(self):
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                self.registry.has_permission(
                    self.collaborator, self.repo_b, 'write',
                )
            with self.assertRaises(TrustsConfigurationError):
                self.registry.filter_authorized(
                    Repository.objects.all(), self.collaborator, 'write',
                )

    def test_authorized_manager_does_not_see_isolated_gh_registry(self):
        """C2 ``AuthorizedManager`` reads configured_handles(), not GH ready()."""
        qs = Repository.objects.authorized(self.collaborator, self.write)
        self.assertIsNone(qs._result_cache)
        with self.assertNumQueries(0):
            self.assertFalse(qs.exists())
