"""GH migration identity is unchanged by the IIb owner lifecycle."""

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from gh_permissions.models import (
    Account,
    AccountRepoGrant,
    Operation,
    Organization,
    PermissionBundle,
    Repository,
    Team,
    TeamRepoGrant,
)
from tests.fixtures import GhFixtureMixin


GH_TABLES = (
    'gh_permissions_account',
    'gh_permissions_organization',
    'gh_permissions_organization_members',
    'gh_permissions_team',
    'gh_permissions_team_members',
    'gh_permissions_operation',
    'gh_permissions_permissionbundle',
    'gh_permissions_permissionbundle_operations',
    'gh_permissions_repository',
    'gh_permissions_accountrepogrant',
    'gh_permissions_teamrepogrant',
)

GH_CONTENT_TYPES = (
    ('gh_permissions', 'account'),
    ('gh_permissions', 'organization'),
    ('gh_permissions', 'team'),
    ('gh_permissions', 'operation'),
    ('gh_permissions', 'permissionbundle'),
    ('gh_permissions', 'repository'),
    ('gh_permissions', 'accountrepogrant'),
    ('gh_permissions', 'teamrepogrant'),
)


class GhMigrationIdentityTests(GhFixtureMixin, TestCase):
    def test_loader_key_and_applied_set_are_unchanged(self):
        loader = MigrationLoader(connection)
        self.assertIn(('gh_permissions', '0001_initial'), loader.disk_migrations)
        self.assertEqual(
            {
                name
                for app, name in loader.applied_migrations
                if app == 'gh_permissions'
            },
            {'0001_initial'},
        )
        initial = loader.disk_migrations[('gh_permissions', '0001_initial')]
        self.assertEqual(
            initial.__module__, 'gh_permissions.migrations.0001_initial',
        )
        self.assertEqual(
            {key[0] for key in loader.disk_migrations} & {'trusts', 'trusts_core'},
            set(),
        )

    def test_schema_tables_and_content_types_use_gh_identity(self):
        table_names = set(connection.introspection.table_names())
        for table in GH_TABLES:
            self.assertIn(table, table_names)
        found = {
            (ct.app_label, ct.model)
            for ct in ContentType.objects.filter(app_label='gh_permissions')
            if ct.model in {row[1] for row in GH_CONTENT_TYPES}
        }
        self.assertEqual(found, set(GH_CONTENT_TYPES))
        for model in (
            Account, Organization, Team, Operation, PermissionBundle,
            Repository, AccountRepoGrant, TeamRepoGrant,
        ):
            self.assertEqual(model._meta.app_label, 'gh_permissions')
            ct = ContentType.objects.get_for_model(model)
            self.assertEqual(ct.app_label, 'gh_permissions')
            self.assertEqual(ct.model, model._meta.model_name)

    def test_representative_rows_keep_persisted_identities(self):
        self.assertEqual(self.org_a.name, 'acme')
        self.assertEqual(self.repo_a.title, 'repo-a')
        self.assertEqual(self.member.name, 'member')
        self.assertEqual(
            AccountRepoGrant.objects.filter(
                account=self.collaborator,
                repository=self.repo_b,
                operation=self.write,
            ).count(),
            1,
        )
        self.assertEqual(
            TeamRepoGrant.objects.filter(
                team=self.writers,
                repository=self.repo_a,
                operation=self.read,
            ).count(),
            1,
        )
        self.assertEqual(Organization.objects.get(pk=self.org_a.pk).name, 'acme')
        self.assertEqual(Repository.objects.get(pk=self.repo_a.pk).title, 'repo-a')

    def test_already_migrated_database_has_no_gh_schema_plan(self):
        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        gh_plan = [
            (migration.app_label, migration.name, backwards)
            for migration, backwards in plan
            if migration.app_label == 'gh_permissions'
        ]
        self.assertEqual(gh_plan, [])

    def test_makemigrations_gh_permissions_check_is_quiet(self):
        try:
            call_command('makemigrations', 'gh_permissions', check=True, verbosity=0)
        except CommandError as exc:
            self.fail('makemigrations gh_permissions --check failed: %s' % exc)
