"""GH kernel topology: one app-owned catalog, no Core app, no Zero."""

import sys

from django.apps import apps
from django.core.checks import run_checks
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase

from trusts.models import AuthorizedManager, AuthorizedQuerySet

import gh_permissions.models as gh_models
from gh_permissions.apps import gh_config
from gh_permissions.models import Repository


class KernelIdentityTest(SimpleTestCase):
    def test_installed_apps_are_gh_only(self):
        labels = [config.label for config in apps.get_app_configs()]
        self.assertIn('gh_permissions', labels)
        self.assertNotIn('trusts_lifecycle', labels)
        self.assertNotIn('trusts_core', labels)
        names = [config.name for config in apps.get_app_configs()]
        self.assertIn('gh_permissions', names)
        self.assertNotIn('trusts.apps', names)
        self.assertNotIn('trusts', names)
        self.assertNotIn('trusts.zero', names)

    def test_repository_uses_stock_authorized_manager(self):
        self.assertIs(type(Repository.objects), AuthorizedManager)
        self.assertIs(Repository.objects._queryset_class, AuthorizedQuerySet)
        self.assertFalse(hasattr(gh_models, 'GhAuthorizedQuerySet'))
        self.assertFalse(hasattr(gh_models, 'GhAuthorizedManager'))
        self.assertFalse(hasattr(Repository.objects, 'permitted'))
        self.assertFalse(hasattr(Repository.objects, 'get_permission'))


class KernelMigrationLoaderTest(TestCase):
    def test_kernel_has_no_trusts_migration_keys(self):
        loader = MigrationLoader(connection)
        keys = {key for key in loader.disk_migrations if key[0] == 'trusts'}
        self.assertEqual(keys, set())
        core_keys = {
            key for key in loader.disk_migrations if key[0] == 'trusts_core'
        }
        self.assertEqual(core_keys, set())
        gh_keys = {
            key for key in loader.disk_migrations if key[0] == 'gh_permissions'
        }
        self.assertEqual(
            gh_keys,
            {
                ('gh_permissions', '0001_initial'),
                ('gh_permissions', '0002_auth_permission_terminal'),
                ('gh_permissions', '0003_organization_owner_permission'),
                ('gh_permissions', '0004_shared_names_ownership_collaborators'),
                ('gh_permissions', '0005_repositorydelegation'),
                ('gh_permissions', '0006_personal_delegations'),
                (
                    'gh_permissions',
                    '0007_issue_issuepermissions_and_more',
                ),
                (
                    'gh_permissions',
                    '0008_rename_delegation_permissions',
                ),
            },
        )


class GhStartupWithoutZeroTest(TestCase):
    def test_gh_repository_loads_and_zero_stays_unimported(self):
        self.assertNotIn('trusts.zero', sys.modules)
        self.assertEqual(Repository._meta.app_label, 'gh_permissions')
        self.assertIs(
            apps.get_model('gh_permissions', 'Repository'),
            Repository,
        )
        self.assertTrue(apps.is_installed('gh_permissions'))
        self.assertNotIn('trusts.zero', sys.modules)
        self.assertIn(Repository, list(gh_config().get_models()))

    def test_system_checks_are_clean_and_zero_sql(self):
        self.assertNotIn('trusts.zero', sys.modules)
        with self.assertNumQueries(0):
            messages = [
                m for m in run_checks()
                if getattr(m, 'id', '').startswith('trusts.E')
            ]
        self.assertEqual(messages, [])
        self.assertNotIn('trusts.zero', sys.modules)
