"""0002 drops Operation-backed grants instead of reusing their ids.

An old ``operation_id`` can equal an unrelated ``auth_permission.id``.
The migration deletes the grant rows before either foreign key changes
target, so that integer is not silently reassigned.
"""

import importlib

from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import connections
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

_migration = importlib.import_module(
    'gh_permissions.migrations.0002_auth_permission_terminal',
)
reverse_delete_legacy_operation_grants = (
    _migration.reverse_delete_legacy_operation_grants
)


_ALIAS = 'gh_permission_reset'


def _wipe(connection):
    """Drop the runner's syncdb tables so 0001 can be applied for real."""
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA foreign_keys = OFF')
        names = connection.introspection.table_names()
        for name in names:
            cursor.execute(
                'DROP TABLE IF EXISTS %s' % connection.ops.quote_name(name)
            )
        cursor.execute('PRAGMA foreign_keys = ON')


class LegacyOperationGrantResetTest(TransactionTestCase):
    databases = {'default', _ALIAS}

    def test_reverse_refuses_to_restore_deleted_grants(self):
        with self.assertRaises(IrreversibleError):
            reverse_delete_legacy_operation_grants(None, None)

    def test_0002_deletes_colliding_operation_grants(self):
        connection = connections[_ALIAS]
        _wipe(connection)
        executor = MigrationExecutor(connection)
        executor.migrate([('gh_permissions', '0001_initial')])
        old_apps = executor.loader.project_state(
            ('gh_permissions', '0001_initial'),
        ).apps

        Operation = old_apps.get_model('gh_permissions', 'Operation')
        Organization = old_apps.get_model('gh_permissions', 'Organization')
        Repository = old_apps.get_model('gh_permissions', 'Repository')
        Team = old_apps.get_model('gh_permissions', 'Team')
        User = old_apps.get_model('example', 'User')
        Direct = old_apps.get_model(
            'gh_permissions', 'UserRepositoryPermission',
        )
        TeamGrant = old_apps.get_model(
            'gh_permissions', 'TeamRepositoryPermission',
        )

        # migrate() does not emit post_migrate, so seed the permission
        # rows a database that already applied 0001 would already hold.
        content_type = ContentType.objects.using(_ALIAS).create(
            app_label='auth', model='user',
        )
        collision = Permission.objects.using(_ALIAS).create(
            content_type=content_type,
            codename='unrelated_existing',
            name='Unrelated existing permission',
        )
        missing_id = (
            Permission.objects.using(_ALIAS).order_by('-pk').first().pk
            + 100000
        )
        self.assertFalse(
            Permission.objects.using(_ALIAS).filter(pk=missing_id).exists()
        )

        colliding = Operation.objects.using(_ALIAS).create(
            id=collision.pk, code='read',
        )
        missing = Operation.objects.using(_ALIAS).create(
            id=missing_id, code='write',
        )
        user = User.objects.using(_ALIAS).create(username='legacy-owner')
        organization = Organization.objects.using(_ALIAS).create(name='legacy')
        repository = Repository.objects.using(_ALIAS).create(
            organization=organization, title='legacy-repo',
        )
        team = Team.objects.using(_ALIAS).create(
            organization=organization, name='legacy-team',
        )
        team.allowed_operations.add(colliding, missing)
        direct = Direct.objects.using(_ALIAS).create(
            user=user, repository=repository, operation=colliding,
        )
        team_grant = TeamGrant.objects.using(_ALIAS).create(
            team=team, repository=repository, operation=missing,
        )
        self.assertEqual(direct.operation_id, collision.pk)
        self.assertEqual(team_grant.operation_id, missing_id)
        self.assertEqual(team.allowed_operations.count(), 2)

        executor.loader.build_graph()
        executor.migrate([
            ('gh_permissions', '0002_auth_permission_terminal'),
        ])

        new_apps = executor.loader.project_state(
            ('gh_permissions', '0002_auth_permission_terminal'),
        ).apps
        NewDirect = new_apps.get_model(
            'gh_permissions', 'UserRepositoryPermission',
        )
        NewTeamGrant = new_apps.get_model(
            'gh_permissions', 'TeamRepositoryPermission',
        )
        NewTeam = new_apps.get_model('gh_permissions', 'Team')
        self.assertEqual(NewDirect.objects.using(_ALIAS).count(), 0)
        self.assertEqual(NewTeamGrant.objects.using(_ALIAS).count(), 0)
        self.assertFalse(
            NewDirect.objects.using(_ALIAS).filter(pk=direct.pk).exists()
        )
        self.assertFalse(
            NewTeamGrant.objects.using(_ALIAS).filter(pk=team_grant.pk).exists()
        )
        self.assertFalse(
            NewDirect.objects.using(_ALIAS).filter(
                operation_id=collision.pk,
            ).exists()
        )
        self.assertFalse(
            NewTeamGrant.objects.using(_ALIAS).filter(
                operation_id=collision.pk,
            ).exists()
        )
        self.assertFalse(
            NewTeamGrant.objects.using(_ALIAS).filter(
                operation_id=missing_id,
            ).exists()
        )
        self.assertEqual(
            NewTeam.objects.using(_ALIAS).get(pk=team.pk)
            .allowed_operations.count(),
            0,
        )
        self.assertTrue(
            Permission.objects.using(_ALIAS).filter(pk=collision.pk).exists()
        )
