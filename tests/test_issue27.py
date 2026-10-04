"""Issue #27: shared names, ownership, and repository collaborators.

Domain services are tested without admin. The owner registration is
attempted with ``is_owner == True`` and left uninstalled: that condition
is outside the public grammar, and the same path without the condition
would authorize non-owners.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, connections, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from trusts.apps import implementation_for_path
from trusts.core import TrustsConfigurationError
from trusts.policy_lock import render_policy_sql_bytes

from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationMembership,
    Repository,
    RepositoryCollaborator,
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

    def test_user_creation_creates_personal_organization_and_owner_membership(self):
        user = create_user('ada')
        organization = user.personal_organization
        self.assertIsNone(organization.name)
        self.assertEqual(organization.personal_user_id, user.pk)
        self.assertEqual(organization.display_name, 'ada')
        membership = OrganizationMembership.objects.get(
            user=user, organization=organization,
        )
        self.assertTrue(membership.is_owner)
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
        OrganizationMembership.objects.create(
            user=first, organization=organization, is_owner=True,
        )
        OrganizationMembership.objects.create(
            user=second, organization=organization, is_owner=True,
        )
        self.assertEqual(
            set(organization.memberships.filter(is_owner=True).values_list(
                'user__username', flat=True,
            )),
            {'first', 'second'},
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

    def test_owner_condition_is_rejected_and_unfiltered_path_over_grants(self):
        handle = isolated_handle()
        with self.assertNumQueries(0):
            with self.assertRaises(TrustsConfigurationError):
                register_organization_owner(handle)
        self.assertEqual(handle.registry.records, ())

        owner = create_user('owner')
        member = create_user('member')
        organization = create_organization('acme')
        OrganizationMembership.objects.create(
            user=owner, organization=organization, is_owner=True,
        )
        OrganizationMembership.objects.create(
            user=member, organization=organization, is_owner=False,
        )
        repository = Repository.objects.create(
            organization=organization, name='app',
        )
        manage = _manage()
        read = repository_permission('read_repository')
        startup = _startup_registry()
        self.assertFalse(startup.has_permission(owner, organization, manage))
        self.assertFalse(startup.has_permission(member, organization, manage))
        self.assertFalse(owner.has_perm(
            'gh_permissions.manage_organization', organization,
        ))
        self.assertFalse(member.has_perm(
            'gh_permissions.manage_organization', organization,
        ))
        self.assertFalse(owner.has_perm(
            'gh_permissions.read_repository', repository,
        ))
        self.assertFalse(member.has_perm(
            'gh_permissions.read_repository', repository,
        ))
        self.assertEqual(owner.get_group_permissions(organization), set())
        self.assertEqual(member.get_group_permissions(repository), set())

        unfiltered = isolated_handle()
        record = unfiltered.register(
            trust=OrganizationMembership,
            user='user',
            permission='organization__owner_group__permissions',
            content='organization',
        )
        self.assertFalse(record.via_group)
        self.assertEqual(
            record.permission_path,
            ('organization', 'owner_group', 'permissions'),
        )
        self.assertIs(record.permission_model, Permission)
        self.assertTrue(
            unfiltered.registry.has_permission(owner, organization, manage),
        )
        self.assertTrue(
            unfiltered.registry.has_permission(member, organization, manage),
        )

        repositories = isolated_handle()
        repository_record = repositories.register(
            trust=OrganizationMembership,
            user='user',
            permission='organization__owner_group__permissions',
            content='organization__repositories',
        )
        self.assertFalse(repository_record.via_group)
        self.assertIs(repository_record.content_model, Repository)
        self.assertTrue(
            repositories.registry.has_permission(member, repository, read),
        )
        self.assertTrue(
            repositories.registry.has_permission(owner, repository, read),
        )

    def test_named_filter_cannot_walk_back_to_membership(self):
        handle = isolated_handle()
        attempts = (
            lambda u, p, o: o.memberships.is_owner == True,  # noqa: E712
            lambda u, p, o: u.organization_memberships.is_owner == True,  # noqa: E712
        )
        for predicate in attempts:
            with self.assertRaises(Exception) as caught:
                handle.add_named_filter(
                    Organization, 'owner', predicate=predicate,
                )
            self.assertIn('Multi-valued', str(caught.exception))
        self.assertEqual(
            handle.registry.get_permission_condition_record(Organization, 'owner'),
            None,
        )

    def test_collaborators_receive_only_selected_repository_permissions(self):
        organization = create_organization('acme')
        repository = Repository.objects.create(
            organization=organization, name='app',
        )
        outsider = create_user('outsider')
        self.assertFalse(OrganizationMembership.objects.filter(
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
        names = [record.root.__name__ for record in _startup_registry().records]
        self.assertEqual(names, [
            'RepositoryCollaborator',
            'TeamRepositoryPermission',
        ])
        rendered = render_policy_sql_bytes(alias='default').decode('utf-8')
        self.assertIn('schema_version: 1', rendered)
        self.assertIn('RepositoryCollaborator', rendered)
        self.assertIn('TeamRepositoryPermission', rendered)
        self.assertNotIn('OrganizationOwnerPermission', rendered)
        self.assertNotIn('UserRepositoryPermission', rendered)
        self.assertNotIn('OrganizationMembership', rendered)
        self.assertNotIn('get_group_permissions', rendered)
        startup_source_models = (
            'Alias',
        )
        for label in startup_source_models:
            self.assertNotIn(label, rendered)


class SharedNameMigrationTest(TransactionTestCase):
    databases = {'default', _ALIAS}

    def test_0004_reserves_existing_organization_names(self):
        connection = connections[_ALIAS]
        _wipe(connection)
        executor = MigrationExecutor(connection)
        executor.migrate([
            ('gh_permissions', '0003_organization_owner_permission'),
        ])
        apps = executor.loader.project_state(
            ('gh_permissions', '0003_organization_owner_permission'),
        ).apps
        HistoricalOrganization = apps.get_model('gh_permissions', 'Organization')
        HistoricalRepository = apps.get_model('gh_permissions', 'Repository')
        HistoricalUser = apps.get_model('example', 'User')
        organization = HistoricalOrganization.objects.using(_ALIAS).create(
            name='legacy-org',
        )
        HistoricalRepository.objects.using(_ALIAS).create(
            organization=organization, title='legacy-repo',
        )
        HistoricalUser.objects.using(_ALIAS).create(username='legacy-user')

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
        migrated = new_apps.get_model(
            'gh_permissions', 'Organization',
        ).objects.using(_ALIAS).get()
        self.assertEqual(migrated.name, 'legacy-org')
        self.assertIsNone(migrated.personal_user_id)
        self.assertEqual(migrated.owner_group.name, OWNER_GROUP_NAME)
        alias_model = new_apps.get_model('gh_permissions', 'Alias')
        self.assertTrue(
            alias_model.objects.using(_ALIAS).filter(name='legacy-org').exists(),
        )
        self.assertFalse(
            alias_model.objects.using(_ALIAS).filter(name='legacy-user').exists(),
        )
        repository = new_apps.get_model(
            'gh_permissions', 'Repository',
        ).objects.using(_ALIAS).get()
        self.assertEqual(repository.name, 'legacy-repo')


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
