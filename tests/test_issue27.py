"""Issue #27: shared names, ownership, and repository collaborators.

Domain services are tested without admin. An ``OrganizationOwnership``
row is the owner grant. Startup registers that path with no condition.
A user with no ownership row is not an owner. Team does not carry
the owner bundle.
"""

from unittest.mock import patch

from django.apps import apps as global_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, connection, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from trusts.apps import implementation_for_path
from trusts.policy_lock import render_policy_sql_bytes

from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.policy import (
    register_collaborator,
    register_organization_owner,
)
from gh_permissions.services import (
    OWNER_GROUP_NAME,
    AliasConflict,
    AliasMissing,
    PersonalOrganizationError,
    create_organization,
    create_user,
    delete_organization,
    delete_user,
    ensure_owner_group,
    rename_organization,
    rename_user,
)
from tests.fixtures import isolated_handle, repository_permission
from tests.test_migration_0002 import _ALIAS, _wipe


def _startup_registry():
    return implementation_for_path(CANONICAL_BACKEND).configured_backend(
        CANONICAL_BACKEND,
    ).registry


def _manage():
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model='organization',
        codename='manage_organization',
    )


class AliasServiceTest(TestCase):
    def test_duplicate_user_and_organization_aliases_fail(self):
        create_user('ada')
        with self.assertRaises(AliasConflict):
            create_user('ada')
        with self.assertRaises(AliasConflict):
            create_organization('ada')
        create_organization('acme')
        with self.assertRaises(AliasConflict):
            create_organization('acme')
        with self.assertRaises(AliasConflict):
            create_user('acme')
        self.assertEqual(Alias.objects.filter(name='ada').count(), 1)
        self.assertEqual(Alias.objects.filter(name='acme').count(), 1)

    def test_conventional_organization_creation_reserves_its_alias(self):
        organization = create_organization('acme')
        self.assertEqual(organization.name, 'acme')
        self.assertIsNone(organization.personal_user_id)
        self.assertTrue(Alias.objects.filter(name='acme').exists())
        self.assertEqual(organization.owner_group.name, OWNER_GROUP_NAME)

    def test_user_creation_creates_personal_organization_and_ownership_row(self):
        user = create_user('ada')
        organization = user.personal_organization
        self.assertIsNone(organization.name)
        self.assertEqual(organization.personal_user_id, user.pk)
        self.assertEqual(organization.display_name, 'ada')
        ownership = OrganizationOwnership.objects.get(
            user=user, organization=organization,
        )
        self.assertEqual(ownership.pk, organization.ownerships.get().pk)
        self.assertEqual(list(organization.owners.all()), [user])
        self.assertEqual(list(user.owned_organizations.all()), [organization])
        self.assertFalse(any(
            field.name == 'is_owner'
            for field in OrganizationOwnership._meta.local_fields
        ))
        self.assertTrue(Alias.objects.filter(name='ada').exists())
        self.assertEqual(organization.owner_group_id, ensure_owner_group().pk)

    def test_personal_organizations_have_no_independent_name(self):
        user = create_user('ada')
        organization = user.personal_organization
        self.assertIsNone(organization.name)
        with self.assertRaises(PersonalOrganizationError):
            rename_organization(organization, 'other')
        with self.assertRaises(PersonalOrganizationError):
            delete_organization(organization)
        organization.refresh_from_db()
        self.assertIsNone(organization.name)
        self.assertTrue(Alias.objects.filter(name='ada').exists())
        self.assertFalse(Alias.objects.filter(name='other').exists())

    def test_both_shapes_and_neither_shape_are_rejected(self):
        user = create_user('ada')
        group = ensure_owner_group()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Organization.objects.create(
                    name='named', personal_user=user, owner_group=group,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Organization.objects.create(owner_group=group)

    def test_rename_updates_the_alias_and_rolls_back_together(self):
        user = create_user('old-user')
        organization = create_organization('old-org')
        rename_user(user, 'new-user')
        rename_organization(organization, 'new-org')
        user.refresh_from_db()
        organization.refresh_from_db()
        self.assertEqual(user.username, 'new-user')
        self.assertEqual(user.personal_organization.display_name, 'new-user')
        self.assertIsNone(user.personal_organization.name)
        self.assertEqual(organization.name, 'new-org')
        self.assertFalse(Alias.objects.filter(name='old-user').exists())
        self.assertFalse(Alias.objects.filter(name='old-org').exists())
        self.assertTrue(Alias.objects.filter(name='new-user').exists())
        self.assertTrue(Alias.objects.filter(name='new-org').exists())

        with self.assertRaises(AliasConflict):
            rename_user(user, 'new-org')
        user.refresh_from_db()
        self.assertEqual(user.username, 'new-user')
        self.assertTrue(Alias.objects.filter(name='new-user').exists())
        self.assertTrue(Alias.objects.filter(name='new-org').exists())

    def test_delete_releases_the_current_alias(self):
        user = create_user('gone')
        personal_id = user.personal_organization.pk
        delete_user(user)
        self.assertFalse(get_user_model().objects.filter(username='gone').exists())
        self.assertFalse(Organization.objects.filter(pk=personal_id).exists())
        self.assertFalse(Alias.objects.filter(name='gone').exists())

        organization = create_organization('going')
        delete_organization(organization)
        self.assertFalse(Organization.objects.filter(name='going').exists())
        self.assertFalse(Alias.objects.filter(name='going').exists())

    def test_failed_user_create_does_not_keep_the_alias(self):
        User = get_user_model()
        with patch.object(
            User.objects, 'create_user', side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                create_user('carol')
        self.assertFalse(Alias.objects.filter(name='carol').exists())
        self.assertFalse(User.objects.filter(username='carol').exists())

    def test_raw_user_create_and_rename_bypass_the_ledger(self):
        User = get_user_model()
        raw = User.objects.create(username='raw')
        self.assertFalse(Alias.objects.filter(name='raw').exists())
        with self.assertRaises(Organization.DoesNotExist):
            raw.personal_organization
        with self.assertRaises(AliasMissing):
            rename_user(raw, 'raw-2')
        raw.refresh_from_db()
        self.assertEqual(raw.username, 'raw')
        with self.assertRaises(AliasConflict):
            create_organization('raw')
        self.assertFalse(Alias.objects.filter(name='raw').exists())

    def test_raw_delete_leaves_the_alias_reserved(self):
        user = create_user('leak')
        user.delete()
        self.assertTrue(Alias.objects.filter(name='leak').exists())
        with self.assertRaises(AliasConflict):
            create_user('leak')

    def test_blank_overlong_and_unchanged_names_stay_put(self):
        with self.assertRaises(ValueError):
            create_user('')
        with self.assertRaises(ValueError):
            create_user('  ada')
        with self.assertRaises(ValueError):
            create_organization('acme ')
        with self.assertRaises(ValueError):
            create_organization('n' * 41)
        with self.assertRaises(TypeError):
            create_user('ada', username='other')
        self.assertFalse(Alias.objects.exists())

        user = create_user('ada')
        organization = create_organization('acme')
        rename_user(user, 'ada')
        rename_organization(organization, 'acme')
        user.refresh_from_db()
        organization.refresh_from_db()
        self.assertEqual(user.username, 'ada')
        self.assertEqual(organization.name, 'acme')
        self.assertEqual(
            set(Alias.objects.values_list('name', flat=True)),
            {'ada', 'acme'},
        )

    def test_raw_organization_without_an_alias_cannot_be_renamed_here(self):
        organization = Organization.objects.create(
            name='raw-org', owner_group=ensure_owner_group(),
        )
        self.assertFalse(Alias.objects.filter(name='raw-org').exists())
        with self.assertRaises(AliasMissing):
            rename_organization(organization, 'renamed-org')
        organization.refresh_from_db()
        self.assertEqual(organization.name, 'raw-org')


class OwnershipAndCollaborationTest(TestCase):
    def test_an_organization_can_have_multiple_owners(self):
        organization = create_organization('acme')
        first = create_user('first')
        second = create_user('second')
        OrganizationOwnership.objects.create(
            user=first, organization=organization,
        )
        OrganizationOwnership.objects.create(
            user=second, organization=organization,
        )
        self.assertEqual(
            set(organization.owners.values_list('username', flat=True)),
            {'first', 'second'},
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OrganizationOwnership.objects.create(
                    user=first, organization=organization,
                )

    def test_seeded_owner_group_holds_the_broad_permissions(self):
        group = Group.objects.get(name=OWNER_GROUP_NAME)
        codenames = set(group.permissions.values_list('codename', flat=True))
        self.assertEqual(codenames, {
            'manage_organization',
            'read_repository',
            'write_repository',
            'admin_repository',
        })
        user = create_user('ada')
        organization = create_organization('acme')
        self.assertEqual(user.personal_organization.owner_group_id, group.pk)
        self.assertEqual(organization.owner_group_id, group.pk)

    def test_ownership_row_is_the_owner_grant(self):
        handle = isolated_handle()
        with self.assertNumQueries(0):
            repository_record = register_organization_owner(handle)
        records = handle.registry.records
        self.assertEqual(len(records), 2)
        self.assertIs(records[0].root, OrganizationOwnership)
        self.assertIs(records[0].content_model, Organization)
        self.assertIs(repository_record.root, OrganizationOwnership)
        self.assertIs(repository_record.content_model, Repository)
        self.assertIsNone(records[0].condition)
        self.assertIsNone(repository_record.condition)
        self.assertFalse(records[0].via_group)
        self.assertFalse(repository_record.via_group)
        self.assertEqual(
            records[0].permission_path,
            ('organization', 'owner_group', 'permissions'),
        )
        self.assertIs(records[0].permission_model, Permission)

        owner = create_user('owner')
        bystander = create_user('bystander')
        organization = create_organization('acme')
        OrganizationOwnership.objects.create(
            user=owner, organization=organization,
        )
        repository = Repository.objects.create(
            organization=organization, name='app',
        )
        manage = _manage()
        read = repository_permission('read_repository')
        write = repository_permission('write_repository')
        admin = repository_permission('admin_repository')
        startup = _startup_registry()
        self.assertTrue(startup.has_permission(owner, organization, manage))
        self.assertFalse(startup.has_permission(bystander, organization, manage))
        self.assertTrue(owner.has_perm(
            'gh_permissions.manage_organization', organization,
        ))
        self.assertFalse(bystander.has_perm(
            'gh_permissions.manage_organization', organization,
        ))
        for permission in (read, write, admin):
            codename = 'gh_permissions.%s' % permission.codename
            self.assertTrue(startup.has_permission(owner, repository, permission))
            self.assertFalse(
                startup.has_permission(bystander, repository, permission),
            )
            self.assertTrue(owner.has_perm(codename, repository))
            self.assertFalse(bystander.has_perm(codename, repository))
        self.assertEqual(owner.get_group_permissions(organization), set())
        self.assertEqual(owner.get_group_permissions(repository), set())
        self.assertEqual(bystander.get_group_permissions(organization), set())

        team = Team.objects.create(organization=organization, name='writers')
        team.members.add(bystander)
        team.allowed_operations.add(read)
        TeamRepositoryPermission.objects.create(
            team=team, repository=repository, operation=read,
        )
        self.assertTrue(bystander.has_perm(
            'gh_permissions.read_repository', repository,
        ))
        self.assertFalse(bystander.has_perm(
            'gh_permissions.write_repository', repository,
        ))
        self.assertFalse(bystander.has_perm(
            'gh_permissions.admin_repository', repository,
        ))
        self.assertFalse(bystander.has_perm(
            'gh_permissions.manage_organization', organization,
        ))
        self.assertNotIn(manage.pk, {
            row.pk for row in startup.permissions_for(bystander, organization)
        })

    def test_collaborators_receive_only_selected_repository_permissions(self):
        organization = create_organization('acme')
        repository = Repository.objects.create(
            organization=organization, name='app',
        )
        outsider = create_user('outsider')
        self.assertFalse(OrganizationOwnership.objects.filter(
            user=outsider, organization=organization,
        ).exists())
        read = repository_permission('read_repository')
        write = repository_permission('write_repository')
        collaboration = RepositoryCollaborator.objects.create(
            user=outsider, repository=repository,
        )
        collaboration.permissions.add(read)
        self.assertTrue(outsider.has_perm(
            'gh_permissions.read_repository', repository,
        ))
        self.assertFalse(outsider.has_perm(
            'gh_permissions.write_repository', repository,
        ))
        self.assertFalse(outsider.has_perm(
            'gh_permissions.admin_repository', repository,
        ))
        self.assertEqual(
            outsider.get_all_permissions(repository),
            {'gh_permissions.read_repository'},
        )
        self.assertEqual(outsider.get_group_permissions(repository), set())
        record = _startup_registry().records[0]
        self.assertIs(record.root, RepositoryCollaborator)
        self.assertFalse(record.via_group)
        self.assertEqual(record.permission_path, ('permissions',))
        self.assertNotIn(write.pk, {
            row.pk for row in _startup_registry().permissions_for(
                outsider, repository,
            )
        })

    def test_repository_names_are_unique_per_organization_only(self):
        first = create_organization('alpha')
        second = create_organization('beta')
        Repository.objects.create(organization=first, name='app')
        Repository.objects.create(organization=second, name='app')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Repository.objects.create(organization=first, name='app')

    def test_deleted_owner_permission_has_no_policy_path(self):
        import gh_permissions.models as models
        self.assertFalse(hasattr(models, 'OrganizationOwnerPermission'))
        self.assertFalse(hasattr(models, 'UserRepositoryPermission'))
        self.assertFalse(hasattr(models, 'OrganizationMembership'))
        names = [record.root.__name__ for record in _startup_registry().records]
        self.assertEqual(names, [
            'RepositoryCollaborator',
            'TeamRepositoryPermission',
            'OrganizationOwnership',
            'OrganizationOwnership',
        ])
        rendered = render_policy_sql_bytes(alias='default').decode('utf-8')
        self.assertIn('schema_version: 1', rendered)
        self.assertIn('RepositoryCollaborator', rendered)
        self.assertIn('TeamRepositoryPermission', rendered)
        self.assertIn('OrganizationOwnership', rendered)
        self.assertNotIn('OrganizationMembership', rendered)
        self.assertNotIn('OrganizationOwnerPermission', rendered)
        self.assertNotIn('UserRepositoryPermission', rendered)
        self.assertNotIn('is_owner', rendered)
        self.assertNotIn('get_group_permissions', rendered)
        startup_source_models = (
            'Alias',
        )
        for label in startup_source_models:
            self.assertNotIn(label, rendered)


class SharedNameMigrationTest(TransactionTestCase):
    databases = {'default', _ALIAS}

    def _permission(self, using, model, codename, name):
        content_type, _created = ContentType.objects.using(using).get_or_create(
            app_label='gh_permissions', model=model,
        )
        permission, _created = Permission.objects.using(using).get_or_create(
            content_type=content_type,
            codename=codename,
            defaults={'name': name},
        )
        return permission

    def test_0004_preserves_grants_and_backfills_users(self):
        reset = connections[_ALIAS]
        _wipe(reset)
        executor = MigrationExecutor(reset)
        executor.migrate([
            ('gh_permissions', '0003_organization_owner_permission'),
        ])
        apps = executor.loader.project_state(
            ('gh_permissions', '0003_organization_owner_permission'),
        ).apps
        HistoricalOrganization = apps.get_model('gh_permissions', 'Organization')
        HistoricalRepository = apps.get_model('gh_permissions', 'Repository')
        HistoricalUser = apps.get_model('example', 'User')
        HistoricalGrant = apps.get_model(
            'gh_permissions', 'OrganizationOwnerPermission',
        )
        HistoricalDirect = apps.get_model(
            'gh_permissions', 'UserRepositoryPermission',
        )
        HistoricalTeam = apps.get_model('gh_permissions', 'Team')
        HistoricalTeamGrant = apps.get_model(
            'gh_permissions', 'TeamRepositoryPermission',
        )
        read = self._permission(
            _ALIAS, 'repository', 'read_repository', 'Can read repository',
        )
        write = self._permission(
            _ALIAS, 'repository', 'write_repository', 'Can write repository',
        )
        manage = self._permission(
            _ALIAS, 'organization', 'manage_organization',
            'Can manage organization',
        )
        # The stored owner operation is intentionally not manage_organization.
        # Forward migration keeps the ownership row and drops that operation.
        # Reverse can only restore manage_organization.
        other_operation = self._permission(
            _ALIAS, 'repository', 'admin_repository',
            'Can administer repository',
        )
        organization = HistoricalOrganization.objects.using(_ALIAS).create(
            name='legacy-org',
        )
        repository = HistoricalRepository.objects.using(_ALIAS).create(
            organization=organization, title='legacy-repo',
        )
        owner = HistoricalUser.objects.using(_ALIAS).create(
            username='legacy-owner', is_active=True,
        )
        direct = HistoricalUser.objects.using(_ALIAS).create(
            username='legacy-direct', is_active=True,
        )
        HistoricalGrant.objects.using(_ALIAS).create(
            organization=organization,
            owner=owner,
            operation_id=other_operation.pk,
        )
        HistoricalDirect.objects.using(_ALIAS).create(
            user=direct, repository=repository, operation_id=read.pk,
        )
        HistoricalDirect.objects.using(_ALIAS).create(
            user=direct, repository=repository, operation_id=write.pk,
        )
        team = HistoricalTeam.objects.using(_ALIAS).create(
            organization=organization, name='legacy-team',
        )
        HistoricalTeamGrant.objects.using(_ALIAS).create(
            team=team, repository=repository, operation_id=read.pk,
        )

        executor.loader.build_graph()
        executor.migrate([
            ('gh_permissions', '0004_shared_names_ownership_collaborators'),
        ])
        new_apps = executor.loader.project_state(
            ('gh_permissions', '0004_shared_names_ownership_collaborators'),
        ).apps
        with self.assertRaises(LookupError):
            new_apps.get_model('gh_permissions', 'OrganizationOwnerPermission')
        with self.assertRaises(LookupError):
            new_apps.get_model('gh_permissions', 'UserRepositoryPermission')
        Organization = new_apps.get_model('gh_permissions', 'Organization')
        conventional = Organization.objects.using(_ALIAS).get(name='legacy-org')
        self.assertIsNone(conventional.personal_user_id)
        self.assertEqual(conventional.owner_group.name, OWNER_GROUP_NAME)
        AliasModel = new_apps.get_model('gh_permissions', 'Alias')
        self.assertTrue(
            AliasModel.objects.using(_ALIAS).filter(name='legacy-org').exists(),
        )
        for username in ('legacy-owner', 'legacy-direct'):
            self.assertTrue(
                AliasModel.objects.using(_ALIAS).filter(name=username).exists(),
            )
            personal = Organization.objects.using(_ALIAS).get(
                personal_user__username=username,
            )
            self.assertIsNone(personal.name)
            self.assertEqual(personal.owner_group_id, conventional.owner_group_id)
        Ownership = new_apps.get_model('gh_permissions', 'OrganizationOwnership')
        self.assertEqual(
            set(Ownership.objects.using(_ALIAS).filter(
                organization=conventional,
            ).values_list('user_id', flat=True)),
            {owner.pk},
        )
        self.assertEqual(Ownership.objects.using(_ALIAS).count(), 3)
        repository = new_apps.get_model(
            'gh_permissions', 'Repository',
        ).objects.using(_ALIAS).get()
        self.assertEqual(repository.name, 'legacy-repo')
        Collaborator = new_apps.get_model(
            'gh_permissions', 'RepositoryCollaborator',
        )
        self.assertEqual(Collaborator.objects.using(_ALIAS).count(), 1)
        collaborator = Collaborator.objects.using(_ALIAS).get()
        self.assertEqual(collaborator.user_id, direct.pk)
        self.assertEqual(collaborator.repository_id, repository.pk)
        self.assertEqual(
            set(collaborator.permissions.values_list('pk', flat=True)),
            {read.pk, write.pk},
        )
        team_grant = new_apps.get_model(
            'gh_permissions', 'TeamRepositoryPermission',
        ).objects.using(_ALIAS).get()
        self.assertEqual(team_grant.operation_id, read.pk)
        self.assertEqual(team_grant.team_id, team.pk)
        repository_pk = repository.pk

        try:
            executor.loader.build_graph()
            executor.migrate([
                ('gh_permissions', '0003_organization_owner_permission'),
            ])
            restored_apps = executor.loader.project_state(
                ('gh_permissions', '0003_organization_owner_permission'),
            ).apps
            restored_direct = restored_apps.get_model(
                'gh_permissions', 'UserRepositoryPermission',
            )
            self.assertEqual(
                set(restored_direct.objects.using(_ALIAS).values_list(
                    'user_id', 'repository_id', 'operation_id',
                )),
                {
                    (direct.pk, repository_pk, read.pk),
                    (direct.pk, repository_pk, write.pk),
                },
            )
            restored_grant = restored_apps.get_model(
                'gh_permissions', 'OrganizationOwnerPermission',
            ).objects.using(_ALIAS).get()
            self.assertEqual(restored_grant.owner_id, owner.pk)
            self.assertEqual(restored_grant.organization_id, organization.pk)
            self.assertEqual(restored_grant.operation_id, manage.pk)
            self.assertNotEqual(restored_grant.operation_id, other_operation.pk)
            self.assertEqual(
                restored_apps.get_model(
                    'gh_permissions', 'Organization',
                ).objects.using(_ALIAS).count(),
                1,
            )
        finally:
            executor.loader.build_graph()
            executor.migrate([
                ('gh_permissions', '0004_shared_names_ownership_collaborators'),
            ])

    def test_0004_aborts_when_a_username_collides_with_an_organization(self):
        reset = connections[_ALIAS]
        _wipe(reset)
        executor = MigrationExecutor(reset)
        executor.migrate([
            ('gh_permissions', '0003_organization_owner_permission'),
        ])
        apps = executor.loader.project_state(
            ('gh_permissions', '0003_organization_owner_permission'),
        ).apps
        HistoricalOrganization = apps.get_model('gh_permissions', 'Organization')
        HistoricalUser = apps.get_model('example', 'User')
        organization = HistoricalOrganization.objects.using(_ALIAS).create(
            name='shared-name',
        )
        user = HistoricalUser.objects.using(_ALIAS).create(username='shared-name')
        HistoricalUser.objects.using(_ALIAS).create(username='untouched-user')
        executor.loader.build_graph()
        with self.assertRaises(RuntimeError) as caught:
            executor.migrate([
                ('gh_permissions', '0004_shared_names_ownership_collaborators'),
            ])
        message = str(caught.exception)
        self.assertIn('stopped before writing', message)
        self.assertIn('user pk=%s' % user.pk, message)
        self.assertIn('username=%r' % 'shared-name', message)
        self.assertIn('organization pk=%s' % organization.pk, message)
        tables = set(reset.introspection.table_names())
        self.assertNotIn('gh_permissions_alias', tables)
        self.assertNotIn('gh_permissions_organizationownership', tables)
        with reset.cursor() as cursor:
            cursor.execute(
                'SELECT username FROM example_user ORDER BY username'
            )
            self.assertEqual(
                [row[0] for row in cursor.fetchall()],
                ['shared-name', 'untouched-user'],
            )
            cursor.execute('SELECT name FROM gh_permissions_organization')
            self.assertEqual([row[0] for row in cursor.fetchall()], ['shared-name'])

    def test_0004_preserves_authorization_on_the_default_database(self):
        create_permissions(
            global_apps.get_app_config('gh_permissions'),
            verbosity=0,
            interactive=False,
        )
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([
                ('gh_permissions', '0003_organization_owner_permission'),
            ])
            apps = executor.loader.project_state(
                ('gh_permissions', '0003_organization_owner_permission'),
            ).apps
            HistoricalOrganization = apps.get_model('gh_permissions', 'Organization')
            HistoricalRepository = apps.get_model('gh_permissions', 'Repository')
            HistoricalUser = apps.get_model('example', 'User')
            HistoricalGrant = apps.get_model(
                'gh_permissions', 'OrganizationOwnerPermission',
            )
            HistoricalDirect = apps.get_model(
                'gh_permissions', 'UserRepositoryPermission',
            )
            manage = Permission.objects.get(
                content_type__app_label='gh_permissions',
                content_type__model='organization',
                codename='manage_organization',
            )
            read = Permission.objects.get(
                content_type__app_label='gh_permissions',
                content_type__model='repository',
                codename='read_repository',
            )
            write = Permission.objects.get(
                content_type__app_label='gh_permissions',
                content_type__model='repository',
                codename='write_repository',
            )
            organization = HistoricalOrganization.objects.create(name='kept-org')
            repository = HistoricalRepository.objects.create(
                organization=organization, title='kept-repo',
            )
            owner = HistoricalUser.objects.create(
                username='kept-owner', is_active=True,
            )
            direct = HistoricalUser.objects.create(
                username='kept-direct', is_active=True,
            )
            HistoricalGrant.objects.create(
                organization_id=organization.pk,
                owner_id=owner.pk,
                operation_id=manage.pk,
            )
            HistoricalDirect.objects.create(
                user_id=direct.pk,
                repository_id=repository.pk,
                operation_id=read.pk,
            )
            HistoricalDirect.objects.create(
                user_id=direct.pk,
                repository_id=repository.pk,
                operation_id=write.pk,
            )
            executor.loader.build_graph()
            executor.migrate([
                ('gh_permissions', '0004_shared_names_ownership_collaborators'),
            ])
            executor.loader.build_graph()
            executor.migrate([
                ('gh_permissions', '0005_repositorydelegation'),
            ])
            ensure_owner_group()
            User = get_user_model()
            owner = User.objects.get(username='kept-owner')
            direct = User.objects.get(username='kept-direct')
            organization = Organization.objects.get(name='kept-org')
            repository = Repository.objects.get(name='kept-repo')
            self.assertTrue(owner.has_perm(
                'gh_permissions.manage_organization', organization,
            ))
            self.assertFalse(direct.has_perm(
                'gh_permissions.manage_organization', organization,
            ))
            self.assertTrue(direct.has_perm(
                'gh_permissions.read_repository', repository,
            ))
            self.assertTrue(direct.has_perm(
                'gh_permissions.write_repository', repository,
            ))
            self.assertFalse(direct.has_perm(
                'gh_permissions.admin_repository', repository,
            ))
            # The owner row grants the seeded group, a superset of the
            # single manage_organization operation stored before 0004.
            for codename in (
                'read_repository', 'write_repository', 'admin_repository',
            ):
                self.assertTrue(owner.has_perm(
                    'gh_permissions.%s' % codename, repository,
                ))
            collaborator = RepositoryCollaborator.objects.get(
                user=direct, repository=repository,
            )
            self.assertEqual(
                set(collaborator.permissions.values_list('codename', flat=True)),
                {'read_repository', 'write_repository'},
            )
            self.assertTrue(Alias.objects.filter(name='kept-owner').exists())
            self.assertTrue(Alias.objects.filter(name='kept-direct').exists())
            self.assertTrue(Alias.objects.filter(name='kept-org').exists())
            self.assertIsNone(owner.personal_organization.name)
            self.assertTrue(OrganizationOwnership.objects.filter(
                user=owner, organization=owner.personal_organization,
            ).exists())
        finally:
            MigrationExecutor(connection).migrate([
                ('gh_permissions', '0005_repositorydelegation'),
            ])


class CollaboratorRegistrationTest(TestCase):
    def test_collaborator_registration_is_zero_sql(self):
        handle = isolated_handle()
        with self.assertNumQueries(0):
            record = register_collaborator(handle)
        self.assertIs(record.root, RepositoryCollaborator)
        self.assertIs(record.content_model, Repository)
        self.assertEqual(record.user_path, ('user',))
        self.assertEqual(record.permission_path, ('permissions',))
        self.assertFalse(record.via_group)
        self.assertIsNone(record.condition)
