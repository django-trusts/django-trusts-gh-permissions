"""Issue #28: authorization-bearing relationship writes.

Domain services are tested without admin. The inquiry is
``manage_organization`` on the organization row locked inside the
write. A submitted instance, a new ownership row, and a permission
bundle are not that inquiry. Ownership update and delete keep an
owner on every surviving conventional organization. ``delete_user``
applies that rule before it deletes anyone.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.db.models.query import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from gh_permissions.admin import OrganizationOwnershipAdmin
from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.policy import register_organization_owner
from gh_permissions.services import (
    CrossOrganizationRelationship,
    DuplicateRelationshipTarget,
    ImmutableOrganizationBoundary,
    LastOrganizationOwner,
    ManagementDenied,
    MissingRelationshipTarget,
    RelationshipWriteError,
    UndefinedRelationshipWrite,
    add_organization_owner,
    create_organization,
    create_repository,
    create_repository_collaborator,
    create_team_repository_permission,
    create_user,
    delete_organization_ownership,
    delete_repository,
    delete_repository_collaborator,
    delete_team,
    delete_team_repository_permission,
    delete_user,
    move_repository_organization,
    move_team_organization,
    replace_collaborator_permissions,
    replace_team_members_and_ceiling,
    update_organization_ownership,
    update_repository_collaborator,
    update_team_repository_permission,
)
from tests.fixtures import isolated_handle, repository_permission


_READ = 'gh_permissions.read_repository'
_WRITE = 'gh_permissions.write_repository'
_MANAGE = 'gh_permissions.manage_organization'
_MISSING = 2 ** 30


def _manage():
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model='organization',
        codename='manage_organization',
    )


def _graph():
    return {
        'ownerships': set(OrganizationOwnership.objects.values_list(
            'user_id', 'organization_id',
        )),
        'teams': list(Team.objects.order_by('pk').values_list(
            'pk', 'name', 'organization_id',
        )),
        'members': {
            team.pk: set(team.members.order_by('pk').values_list(
                'pk', flat=True,
            ))
            for team in Team.objects.order_by('pk')
        },
        'ceilings': {
            team.pk: set(team.allowed_operations.order_by('pk').values_list(
                'pk', flat=True,
            ))
            for team in Team.objects.order_by('pk')
        },
        'repositories': list(Repository.objects.order_by('pk').values_list(
            'pk', 'name', 'organization_id',
        )),
        'collaborators': list(
            RepositoryCollaborator.objects.order_by('pk').values_list(
                'pk', 'user_id', 'repository_id',
            )
        ),
        'bundles': {
            row.pk: set(row.permissions.order_by('pk').values_list(
                'pk', flat=True,
            ))
            for row in RepositoryCollaborator.objects.order_by('pk')
        },
        'grants': list(
            TeamRepositoryPermission.objects.order_by('pk').values_list(
                'pk', 'team_id', 'repository_id', 'operation_id',
            )
        ),
    }


def _assert_no_mutation(test, exc_type, call):
    before = _graph()
    with CaptureQueriesContext(connection) as captured:
        with test.assertRaises(exc_type):
            call()
    test.assertEqual(_graph(), before)
    for query in captured.captured_queries:
        sql = query['sql'].lstrip().upper()
        test.assertFalse(
            sql.startswith(('INSERT', 'UPDATE', 'DELETE')),
            query['sql'],
        )


def _assert_grant(test, user, repository, permission, codename, granted):
    User = get_user_model()
    test.assertEqual(user.has_perm(codename, repository), granted)
    listed = repository in set(
        Repository.objects.authorized(user, permission)
    )
    test.assertEqual(listed, granted)
    by_code = set(User.objects.permitted(repository, codename))
    by_row = set(User.objects.permitted(repository, permission))
    on_code = set(repository.get_permitted_users(codename))
    on_row = set(repository.get_permitted_users(permission))
    test.assertEqual(by_code, by_row)
    test.assertEqual(by_code, on_code)
    test.assertEqual(by_code, on_row)
    test.assertEqual(user in by_code, granted)


class RelationshipWriteTests(TestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_user('owner')
        self.other_owner = create_user('other-owner')
        self.member = create_user('member')
        self.collaborator = create_user('collaborator')
        self.unrelated = create_user('unrelated')
        self.target = create_user('target')
        self.org = create_organization('acme')
        self.other_org = create_organization('other')
        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.org,
        )
        OrganizationOwnership.objects.create(
            user=self.other_owner, organization=self.other_org,
        )
        self.team = Team.objects.create(organization=self.org, name='writers')
        self.readers = Team.objects.create(organization=self.org, name='readers')
        self.other_team = Team.objects.create(
            organization=self.other_org, name='outsiders',
        )
        self.read = repository_permission('read_repository')
        self.write = repository_permission('write_repository')
        self.admin = repository_permission('admin_repository')
        self.manage = _manage()
        self.team.members.add(self.member)
        self.team.allowed_operations.add(self.read)
        self.repo = Repository.objects.create(
            organization=self.org, name='app',
        )
        self.second_repo = Repository.objects.create(
            organization=self.org, name='second',
        )
        self.other_repo = Repository.objects.create(
            organization=self.other_org, name='foreign',
        )
        self.grant = TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.repo, operation=self.read,
        )
        self.collaboration = RepositoryCollaborator.objects.create(
            user=self.collaborator, repository=self.repo,
        )
        self.collaboration.permissions.add(self.write)

    def _denied_actors(self):
        return (
            self.member,
            self.collaborator,
            self.unrelated,
            self.other_owner,
        )

    def _owner_row(self):
        return OrganizationOwnership.objects.get(
            user=self.owner, organization=self.org,
        )

    def _writes(self, actor):
        return (
            lambda: add_organization_owner(
                actor, self.org.pk, self.target.pk,
            ),
            lambda: replace_team_members_and_ceiling(
                actor, self.team.pk, [self.target.pk], [self.write.pk],
            ),
            lambda: create_repository_collaborator(
                actor, self.second_repo.pk, self.target.pk, [self.read.pk],
            ),
            lambda: update_repository_collaborator(
                actor, self.collaboration.pk, user_id=self.target.pk,
            ),
            lambda: replace_collaborator_permissions(
                actor, self.collaboration.pk, [self.read.pk],
            ),
            lambda: delete_repository_collaborator(
                actor, self.collaboration.pk,
            ),
            lambda: create_team_repository_permission(
                actor, self.team.pk, self.second_repo.pk, self.read.pk,
            ),
            lambda: update_team_repository_permission(
                actor, self.grant.pk, team_id=self.readers.pk,
            ),
            lambda: delete_team_repository_permission(actor, self.grant.pk),
            lambda: delete_team(actor, self.team.pk),
            lambda: create_repository(actor, self.org.pk, 'smuggled'),
            lambda: delete_repository(actor, self.repo.pk),
            lambda: update_organization_ownership(
                actor, self._owner_row().pk, user_id=self.target.pk,
            ),
            lambda: delete_organization_ownership(
                actor, self._owner_row().pk,
            ),
        )

    def test_owner_can_perform_each_settled_write(self):
        added = add_organization_owner(
            self.owner, self.org.pk, self.target.pk,
        )
        self.assertEqual(added.user_id, self.target.pk)
        self.assertEqual(added.organization_id, self.org.pk)
        self.assertTrue(self.target.has_perm(_MANAGE, self.org))

        replace_team_members_and_ceiling(
            self.owner,
            self.team.pk,
            [self.target.pk],
            [self.write.pk],
        )
        self.assertEqual(set(self.team.members.all()), {self.target})
        self.assertEqual(
            set(self.team.allowed_operations.all()), {self.write},
        )
        self.assertEqual(self.team.organization_id, self.org.pk)

        created = create_repository_collaborator(
            self.owner, self.second_repo.pk, self.unrelated.pk, [self.read.pk],
        )
        self.assertEqual(
            set(created.permissions.all()), {self.read},
        )
        update_repository_collaborator(
            self.owner, created.pk, user_id=self.member.pk,
        )
        created.refresh_from_db()
        self.assertEqual(created.user_id, self.member.pk)
        self.assertEqual(set(created.permissions.all()), {self.read})
        update_repository_collaborator(
            self.owner, created.pk, repository_id=self.repo.pk,
        )
        created.refresh_from_db()
        self.assertEqual(created.repository_id, self.repo.pk)
        replace_collaborator_permissions(
            self.owner, created.pk, [self.admin.pk],
        )
        self.assertEqual(set(created.permissions.all()), {self.admin})
        delete_repository_collaborator(self.owner, created.pk)
        self.assertFalse(
            RepositoryCollaborator.objects.filter(pk=created.pk).exists()
        )

        grant = create_team_repository_permission(
            self.owner, self.team.pk, self.second_repo.pk, self.write.pk,
        )
        update_team_repository_permission(
            self.owner, grant.pk, team_id=self.readers.pk,
        )
        grant.refresh_from_db()
        self.assertEqual(grant.team_id, self.readers.pk)
        update_team_repository_permission(
            self.owner, grant.pk, permission_id=self.read.pk,
        )
        grant.refresh_from_db()
        self.assertEqual(grant.operation_id, self.read.pk)
        self.assertEqual(grant.repository_id, self.second_repo.pk)
        delete_team_repository_permission(self.owner, grant.pk)
        self.assertFalse(
            TeamRepositoryPermission.objects.filter(pk=grant.pk).exists()
        )

        team_pk = self.readers.pk
        delete_team(self.owner, team_pk)
        self.assertFalse(Team.objects.filter(pk=team_pk).exists())
        self.assertTrue(Repository.objects.filter(pk=self.repo.pk).exists())
        self.assertEqual(self.team.organization_id, self.org.pk)

        made = create_repository(self.owner, self.org.pk, 'created')
        self.assertEqual(made.name, 'created')
        self.assertEqual(made.organization_id, self.org.pk)
        child = RepositoryCollaborator.objects.create(
            user=self.target, repository=made,
        )
        child_grant = TeamRepositoryPermission.objects.create(
            team=self.team, repository=made, operation=self.read,
        )
        delete_repository(self.owner, made.pk)
        self.assertFalse(Repository.objects.filter(pk=made.pk).exists())
        self.assertFalse(
            RepositoryCollaborator.objects.filter(pk=child.pk).exists()
        )
        self.assertFalse(
            TeamRepositoryPermission.objects.filter(pk=child_grant.pk).exists()
        )
        self.assertTrue(Repository.objects.filter(pk=self.repo.pk).exists())
        self.assertTrue(
            RepositoryCollaborator.objects.filter(
                pk=self.collaboration.pk,
            ).exists()
        )
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(pk=self.grant.pk).exists()
        )

    def test_member_collaborator_stranger_and_other_owner_are_denied(self):
        for actor in self._denied_actors():
            for call in self._writes(actor):
                _assert_no_mutation(self, ManagementDenied, call)

    def test_forged_organization_team_and_repository_ids_fail(self):
        calls = (
            lambda: add_organization_owner(
                self.owner, _MISSING, self.target.pk,
            ),
            lambda: replace_team_members_and_ceiling(
                self.owner, _MISSING, [self.target.pk], [self.read.pk],
            ),
            lambda: create_repository_collaborator(
                self.owner, _MISSING, self.target.pk, [self.read.pk],
            ),
            lambda: create_team_repository_permission(
                self.owner, _MISSING, self.repo.pk, self.read.pk,
            ),
            lambda: create_team_repository_permission(
                self.owner, self.team.pk, _MISSING, self.read.pk,
            ),
            lambda: update_repository_collaborator(
                self.owner, self.collaboration.pk, repository_id=_MISSING,
            ),
            lambda: update_team_repository_permission(
                self.owner, self.grant.pk, team_id=_MISSING,
            ),
            lambda: update_team_repository_permission(
                self.owner, self.grant.pk, repository_id=_MISSING,
            ),
            lambda: delete_team(self.owner, _MISSING),
            lambda: create_repository(self.owner, _MISSING, 'missing-org'),
            lambda: delete_repository(self.owner, _MISSING),
            lambda: delete_repository_collaborator(self.owner, _MISSING),
            lambda: delete_team_repository_permission(self.owner, _MISSING),
        )
        for call in calls:
            _assert_no_mutation(self, MissingRelationshipTarget, call)

    def test_submitted_instances_are_not_authorization_evidence(self):
        proposed_org = Organization(
            name='forged', owner_group=self.org.owner_group,
        )
        proposed_row = OrganizationOwnership(
            user=self.member, organization=self.org,
        )
        proposed_permission = Permission(
            codename='manage_organization',
            name='forged',
            content_type_id=self.manage.content_type_id,
        )
        self.team.organization = self.other_org
        self.assertIsNone(proposed_org.pk)
        self.assertIsNone(proposed_row.pk)
        self.assertIsNone(proposed_permission.pk)
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: add_organization_owner(
                self.member, proposed_org, self.member.pk,
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: add_organization_owner(
                self.member, self.org.pk, proposed_row,
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: replace_collaborator_permissions(
                self.owner, self.collaboration.pk, [proposed_permission],
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: replace_team_members_and_ceiling(
                self.owner, self.team, [self.member.pk], [self.read.pk],
            ),
        )
        self.team.refresh_from_db()
        self.assertEqual(self.team.organization_id, self.org.pk)

    def test_stored_manage_permission_does_not_authorize_the_actor(self):
        self.collaboration.permissions.add(self.manage)
        self.team.allowed_operations.add(self.manage)
        TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.repo, operation=self.manage,
        )
        self.assertTrue(self.collaborator.has_perm(_MANAGE, self.repo))
        self.assertTrue(self.member.has_perm(_MANAGE, self.repo))
        self.assertFalse(self.collaborator.has_perm(_MANAGE, self.org))
        self.assertFalse(self.member.has_perm(_MANAGE, self.org))
        self.assertNotIn(
            self.org,
            set(Organization.objects.authorized(self.collaborator, self.manage)),
        )
        self.assertNotIn(
            self.org,
            set(Organization.objects.authorized(self.member, self.manage)),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: delete_repository_collaborator(
                self.collaborator, self.collaboration.pk,
            ),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: delete_team(self.member, self.team.pk),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: delete_repository(self.collaborator, self.repo.pk),
        )

    def test_adding_an_owner_does_not_authorize_the_actor(self):
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: add_organization_owner(
                self.member, self.org.pk, self.member.pk,
            ),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: add_organization_owner(
                self.member, self.org.pk, self.owner.pk,
            ),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: add_organization_owner(
                self.target, self.org.pk, self.unrelated.pk,
            ),
        )
        self.assertFalse(self.member.has_perm(_MANAGE, self.org))
        self.assertNotIn(
            self.org,
            set(Organization.objects.authorized(self.member, self.manage)),
        )
        add_organization_owner(self.owner, self.org.pk, self.target.pk)
        self.assertTrue(self.target.has_perm(_MANAGE, self.org))
        self.assertIn(
            self.org,
            set(Organization.objects.authorized(self.target, self.manage)),
        )
        self.assertFalse(self.member.has_perm(_MANAGE, self.org))
        add_organization_owner(self.target, self.org.pk, self.unrelated.pk)
        self.assertTrue(
            OrganizationOwnership.objects.filter(
                user=self.unrelated, organization=self.org,
            ).exists()
        )

    def test_in_memory_superuser_flag_is_not_the_bypass(self):
        self.member.is_superuser = True
        self.member.is_active = True
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: add_organization_owner(
                self.member, self.org.pk, self.target.pk,
            ),
        )
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_superuser)

    def test_bundle_changes_agree_across_the_four_inquiries(self):
        TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.repo, operation=self.write,
        )
        _assert_grant(
            self, self.member, self.repo, self.read, _READ, True,
        )
        _assert_grant(
            self, self.member, self.repo, self.write, _WRITE, False,
        )
        _assert_grant(
            self, self.collaborator, self.repo, self.write, _WRITE, True,
        )
        _assert_grant(
            self, self.collaborator, self.repo, self.read, _READ, False,
        )

        replace_team_members_and_ceiling(
            self.owner,
            self.team.pk,
            [self.member.pk],
            [self.read.pk, self.write.pk],
        )
        _assert_grant(
            self, self.member, self.repo, self.read, _READ, True,
        )
        _assert_grant(
            self, self.member, self.repo, self.write, _WRITE, True,
        )

        replace_team_members_and_ceiling(
            self.owner,
            self.team.pk,
            [self.member.pk],
            [self.write.pk],
        )
        _assert_grant(
            self, self.member, self.repo, self.read, _READ, False,
        )
        _assert_grant(
            self, self.member, self.repo, self.write, _WRITE, True,
        )

        replace_collaborator_permissions(
            self.owner, self.collaboration.pk, [self.read.pk],
        )
        _assert_grant(
            self, self.collaborator, self.repo, self.read, _READ, True,
        )
        _assert_grant(
            self, self.collaborator, self.repo, self.write, _WRITE, False,
        )

        replace_collaborator_permissions(
            self.owner, self.collaboration.pk, [],
        )
        _assert_grant(
            self, self.collaborator, self.repo, self.read, _READ, False,
        )
        _assert_grant(
            self, self.collaborator, self.repo, self.write, _WRITE, False,
        )

    def test_failed_multi_statement_writes_roll_back(self):
        before = _graph()
        ceiling = type(self.team.allowed_operations)
        with CaptureQueriesContext(connection) as captured:
            with patch.object(ceiling, 'set', side_effect=IntegrityError('ceiling')):
                with self.assertRaises(IntegrityError):
                    replace_team_members_and_ceiling(
                        self.owner,
                        self.team.pk,
                        [self.target.pk],
                        [self.write.pk],
                    )
        inserted = [
            query['sql'] for query in captured.captured_queries
            if query['sql'].lstrip().upper().startswith('INSERT')
        ]
        self.assertTrue(inserted)
        self.assertEqual(_graph(), before)

        bundle = type(self.collaboration.permissions)
        with CaptureQueriesContext(connection) as captured:
            with patch.object(bundle, 'set', side_effect=IntegrityError('bundle')):
                with self.assertRaises(IntegrityError):
                    create_repository_collaborator(
                        self.owner,
                        self.second_repo.pk,
                        self.target.pk,
                        [self.read.pk],
                    )
        inserted = [
            query['sql'] for query in captured.captured_queries
            if query['sql'].lstrip().upper().startswith('INSERT')
        ]
        self.assertTrue(inserted)
        self.assertEqual(_graph(), before)
        self.assertFalse(
            RepositoryCollaborator.objects.filter(
                user=self.target, repository=self.second_repo,
            ).exists()
        )

    def test_collaborator_update_authorizes_stored_and_replacement_orgs(self):
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: update_repository_collaborator(
                self.owner,
                self.collaboration.pk,
                repository_id=self.other_repo.pk,
            ),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: update_repository_collaborator(
                self.other_owner,
                self.collaboration.pk,
                repository_id=self.other_repo.pk,
            ),
        )
        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.other_org,
        )
        update_repository_collaborator(
            self.owner,
            self.collaboration.pk,
            repository_id=self.other_repo.pk,
        )
        self.collaboration.refresh_from_db()
        self.assertEqual(self.collaboration.repository_id, self.other_repo.pk)
        self.assertEqual(
            set(self.collaboration.permissions.all()), {self.write},
        )
        self.assertEqual(self.repo.organization_id, self.org.pk)
        self.assertEqual(self.other_repo.organization_id, self.other_org.pk)

    def test_team_grant_update_checks_both_boundaries_and_equality(self):
        update_team_repository_permission(
            self.owner, self.grant.pk, team_id=self.readers.pk,
        )
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.team_id, self.readers.pk)

        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: update_team_repository_permission(
                self.owner,
                self.grant.pk,
                repository_id=self.other_repo.pk,
            ),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: update_team_repository_permission(
                self.other_owner,
                self.grant.pk,
                team_id=self.other_team.pk,
            ),
        )
        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.other_org,
        )
        _assert_no_mutation(
            self,
            CrossOrganizationRelationship,
            lambda: update_team_repository_permission(
                self.owner,
                self.grant.pk,
                repository_id=self.other_repo.pk,
            ),
        )
        _assert_no_mutation(
            self,
            CrossOrganizationRelationship,
            lambda: create_team_repository_permission(
                self.owner,
                self.team.pk,
                self.other_repo.pk,
                self.read.pk,
            ),
        )
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.repository_id, self.repo.pk)
        self.assertEqual(self.team.organization_id, self.org.pk)

    def test_queryset_create_can_store_what_the_service_rejects(self):
        stored = TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.other_repo, operation=self.read,
        )
        with self.assertRaises(ValidationError):
            stored.clean()
        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.other_org,
        )
        _assert_no_mutation(
            self,
            CrossOrganizationRelationship,
            lambda: delete_team_repository_permission(self.owner, stored.pk),
        )
        _assert_no_mutation(
            self,
            CrossOrganizationRelationship,
            lambda: update_team_repository_permission(
                self.owner, stored.pk, permission_id=self.write.pk,
            ),
        )

    def test_duplicates_and_undefined_shapes_raise_before_a_write(self):
        _assert_no_mutation(
            self,
            DuplicateRelationshipTarget,
            lambda: add_organization_owner(
                self.owner, self.org.pk, self.owner.pk,
            ),
        )
        _assert_no_mutation(
            self,
            DuplicateRelationshipTarget,
            lambda: replace_team_members_and_ceiling(
                self.owner,
                self.team.pk,
                [self.member.pk, self.member.pk],
                [self.read.pk],
            ),
        )
        _assert_no_mutation(
            self,
            DuplicateRelationshipTarget,
            lambda: create_repository_collaborator(
                self.owner, self.repo.pk, self.collaborator.pk, [self.read.pk],
            ),
        )
        _assert_no_mutation(
            self,
            DuplicateRelationshipTarget,
            lambda: create_team_repository_permission(
                self.owner, self.team.pk, self.repo.pk, self.read.pk,
            ),
        )
        _assert_no_mutation(
            self,
            DuplicateRelationshipTarget,
            lambda: create_repository(self.owner, self.org.pk, self.repo.name),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: create_repository(self.owner, self.org.pk, ''),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: create_repository(self.owner, self.org.pk, ' padded'),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: create_repository(self.owner, True, 'nope'),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: update_repository_collaborator(
                self.owner, self.collaboration.pk,
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: update_team_repository_permission(
                self.owner, self.grant.pk,
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: replace_collaborator_permissions(
                self.owner, self.collaboration.pk, 'read',
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: add_organization_owner(
                self.owner, True, self.target.pk,
            ),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: replace_collaborator_permissions(
                self.owner, self.collaboration.pk, [_MISSING],
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: add_organization_owner(
                get_user_model()(username='unsaved'),
                self.org.pk,
                self.target.pk,
            ),
        )

    def test_organization_lock_precedes_the_inquiry_inside_atomic(self):
        import gh_permissions.services as services

        events = []
        original_fetch = QuerySet._fetch_all
        original_atomic = services.transaction.atomic
        original_authorized = Organization.objects.authorized

        def fetch(queryset):
            if queryset.query.select_for_update:
                events.append(('lock', queryset.model))
            return original_fetch(queryset)

        class RecordingAtomic(object):
            def __init__(self, context):
                self.context = context

            def __enter__(self):
                events.append('atomic')
                return self.context.__enter__()

            def __exit__(self, exc_type, exc, tb):
                return self.context.__exit__(exc_type, exc, tb)

        def atomic(*args, **kwargs):
            return RecordingAtomic(original_atomic(*args, **kwargs))

        def authorized(self, user, permission, extra_q=None):
            events.append('inquiry')
            return original_authorized(user, permission, extra_q=extra_q)

        with patch.object(QuerySet, '_fetch_all', fetch):
            with patch.object(services.transaction, 'atomic', atomic):
                with patch.object(
                    type(Organization.objects), 'authorized', authorized,
                ):
                    add_organization_owner(
                        self.owner, self.org.pk, self.target.pk,
                    )
        self.assertIn('atomic', events)
        lock_at = events.index(('lock', Organization))
        inquiry_at = events.index('inquiry')
        self.assertLess(events.index('atomic'), lock_at)
        self.assertLess(lock_at, inquiry_at)

    def test_active_superuser_bypass_does_not_invent_ownership(self):
        User = get_user_model()
        superuser = User.objects.create_superuser(
            username='root', email='root@example.com', password='secret',
        )
        self.assertTrue(superuser.is_active and superuser.is_superuser)
        self.assertFalse(
            OrganizationOwnership.objects.filter(user=superuser).exists()
        )
        self.assertNotIn(
            self.org,
            set(Organization.objects.authorized(superuser, self.manage)),
        )
        self.assertNotIn(
            self.repo,
            set(Repository.objects.authorized(superuser, self.read)),
        )
        add_organization_owner(superuser, self.org.pk, self.target.pk)
        replace_collaborator_permissions(
            superuser, self.collaboration.pk, [self.read.pk],
        )
        made = create_repository(superuser, self.org.pk, 'root-repo')
        self.assertEqual(made.organization_id, self.org.pk)
        delete_repository(superuser, made.pk)
        self.assertFalse(Repository.objects.filter(pk=made.pk).exists())
        self.assertFalse(
            OrganizationOwnership.objects.filter(
                user=superuser, organization=self.org,
            ).exists()
        )
        self.assertNotIn(
            self.org,
            set(Organization.objects.authorized(superuser, self.manage)),
        )
        self.assertNotIn(
            self.repo,
            set(Repository.objects.authorized(superuser, self.read)),
        )
        self.assertTrue(superuser.has_perm(_MANAGE, self.org))
        self.assertEqual(
            set(self.collaboration.permissions.all()), {self.read},
        )

    def test_inactive_ordinary_owner_is_denied(self):
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)
        self.assertFalse(self.owner.is_superuser)
        self.assertTrue(
            OrganizationOwnership.objects.filter(
                user=self.owner, organization=self.org,
            ).exists()
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: add_organization_owner(
                self.owner, self.org.pk, self.target.pk,
            ),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: delete_team(self.owner, self.team.pk),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: create_repository(self.owner, self.org.pk, 'idle-repo'),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: delete_repository(self.owner, self.repo.pk),
        )

    def test_inactive_superuser_is_denied_even_with_ownership(self):
        User = get_user_model()
        superuser = User.objects.create_superuser(
            username='root', email='root@example.com', password='secret',
        )
        superuser.is_active = False
        superuser.save(update_fields=['is_active'])
        self.assertTrue(superuser.is_superuser)
        self.assertFalse(superuser.is_active)
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: add_organization_owner(
                superuser, self.org.pk, self.target.pk,
            ),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: delete_team(superuser, self.team.pk),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: delete_repository(superuser, self.repo.pk),
        )
        OrganizationOwnership.objects.create(
            user=superuser, organization=self.org,
        )
        self.assertIn(
            self.org,
            set(Organization.objects.authorized(superuser, self.manage)),
        )
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: add_organization_owner(
                superuser, self.org.pk, self.target.pk,
            ),
        )
        self.assertFalse(
            OrganizationOwnership.objects.filter(
                user=self.target, organization=self.org,
            ).exists()
        )

    def test_superuser_still_rejects_missing_and_cross_organization_parents(self):
        User = get_user_model()
        superuser = User.objects.create_superuser(
            username='root', email='root@example.com', password='secret',
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: delete_team(superuser, _MISSING),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: create_repository(superuser, _MISSING, 'missing'),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: delete_repository(superuser, _MISSING),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: create_repository_collaborator(
                superuser, _MISSING, self.target.pk, [self.read.pk],
            ),
        )
        _assert_no_mutation(
            self,
            CrossOrganizationRelationship,
            lambda: create_team_repository_permission(
                superuser, self.team.pk, self.other_repo.pk, self.read.pk,
            ),
        )
        stored = TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.other_repo, operation=self.write,
        )
        _assert_no_mutation(
            self,
            CrossOrganizationRelationship,
            lambda: delete_team_repository_permission(superuser, stored.pk),
        )

    def test_admin_calls_the_relationship_services(self):
        import inspect

        from gh_permissions import admin as admin_module

        source = inspect.getsource(admin_module)
        for name in (
            'add_organization_owner',
            'replace_team_members_and_ceiling',
            'create_repository_collaborator',
            'update_repository_collaborator',
            'delete_repository_collaborator',
            'replace_collaborator_permissions',
            'create_team_repository_permission',
            'update_team_repository_permission',
            'delete_team_repository_permission',
            'update_organization_ownership',
            'delete_organization_ownership',
            'move_team_organization',
            'move_repository_organization',
            'create_repository',
            'delete_repository',
        ):
            self.assertIn(name, source)
        self.assertIn('delete_team(', source)
        self.assertIn('create_repository(', source)
        self.assertIn('delete_repository(', source)
        self.assertNotIn('form.save_m2m', source)
        self.assertFalse(OrganizationOwnershipAdmin.scope_allows_add)
        self.assertFalse(OrganizationOwnershipAdmin.scope_allows_change)
        self.assertFalse(OrganizationOwnershipAdmin.scope_allows_delete)

    def test_first_owner_of_an_ownerless_organization_needs_the_bypass(self):
        bare = create_organization('bare')
        self.assertFalse(bare.ownerships.exists())
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: add_organization_owner(
                self.owner, bare.pk, self.owner.pk,
            ),
        )
        User = get_user_model()
        superuser = User.objects.create_superuser(
            username='root', email='root@example.com', password='secret',
        )
        add_organization_owner(superuser, bare.pk, self.owner.pk)
        self.assertTrue(self.owner.has_perm(_MANAGE, bare))
        self.assertNotIn(
            bare,
            set(Organization.objects.authorized(superuser, self.manage)),
        )


def _assert_inquiry_not_called(test, call):
    events = []
    original = Organization.objects.authorized

    def authorized(self, user, permission, extra_q=None):
        events.append('inquiry')
        return original(user, permission, extra_q)

    try:
        with patch.object(type(Organization.objects), 'authorized', authorized):
            call()
    finally:
        test.assertEqual(events, [])


class OwnershipRetentionTests(TestCase):
    """Surviving conventional organizations keep an owner.

    The check is the service count under the locked organization row.
    ``register(condition=)`` stays empty on the owner registration.
    """

    def setUp(self):
        super().setUp()
        self.owner = create_user('owner')
        self.other = create_user('other')
        self.member = create_user('member')
        self.org = create_organization('acme')
        self.other_org = create_organization('other-org')
        self.row = OrganizationOwnership.objects.create(
            user=self.owner, organization=self.org,
        )
        OrganizationOwnership.objects.create(
            user=self.other, organization=self.other_org,
        )
        self.manage = _manage()

    def _superuser(self, username='root'):
        return get_user_model().objects.create_superuser(
            username=username, email='%s@example.com' % username, password='secret',
        )

    def test_another_owner_may_be_removed_or_reassigned(self):
        second = add_organization_owner(
            self.owner, self.org.pk, self.member.pk,
        )
        delete_organization_ownership(self.owner, second.pk)
        self.assertFalse(
            OrganizationOwnership.objects.filter(pk=second.pk).exists()
        )
        self.assertEqual(self.org.ownerships.count(), 1)
        self.assertTrue(self.owner.has_perm(_MANAGE, self.org))
        self.assertFalse(self.member.has_perm(_MANAGE, self.org))
        self.assertNotIn(
            self.org,
            set(Organization.objects.authorized(self.member, self.manage)),
        )

        second = add_organization_owner(
            self.owner, self.org.pk, self.member.pk,
        )
        fresh = create_user('fresh')
        updated = update_organization_ownership(
            self.owner, second.pk, user_id=fresh.pk,
        )
        self.assertEqual(updated.user_id, fresh.pk)
        self.assertEqual(updated.organization_id, self.org.pk)
        self.assertTrue(fresh.has_perm(_MANAGE, self.org))
        self.assertFalse(self.member.has_perm(_MANAGE, self.org))
        self.assertIn(
            self.org,
            set(Organization.objects.authorized(fresh, self.manage)),
        )

        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.other_org,
        )
        moved = update_organization_ownership(
            self.owner, updated.pk, organization_id=self.other_org.pk,
        )
        self.assertEqual(moved.user_id, fresh.pk)
        self.assertEqual(moved.organization_id, self.other_org.pk)
        self.assertEqual(
            set(self.org.ownerships.values_list('user_id', flat=True)),
            {self.owner.pk},
        )
        self.assertTrue(self.owner.has_perm(_MANAGE, self.org))
        self.assertTrue(fresh.has_perm(_MANAGE, self.other_org))

    def test_sole_owner_may_be_replaced_on_the_same_organization(self):
        updated = update_organization_ownership(
            self.owner, self.row.pk, user_id=self.member.pk,
        )
        self.assertEqual(updated.organization_id, self.org.pk)
        self.assertEqual(self.org.ownerships.count(), 1)
        self.assertTrue(self.member.has_perm(_MANAGE, self.org))
        self.assertFalse(self.owner.has_perm(_MANAGE, self.org))
        self.assertIn(
            self.org,
            set(Organization.objects.authorized(self.member, self.manage)),
        )
        self.assertNotIn(
            self.org,
            set(Organization.objects.authorized(self.owner, self.manage)),
        )

    def test_last_conventional_owner_cannot_be_deleted_or_moved(self):
        before = _graph()
        with self.assertRaises(LastOrganizationOwner) as caught:
            delete_organization_ownership(self.owner, self.row.pk)
        self.assertEqual(caught.exception.organization_ids, (self.org.pk,))
        self.assertEqual(_graph(), before)

        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.other_org,
        )
        with self.assertRaises(LastOrganizationOwner) as caught:
            update_organization_ownership(
                self.owner,
                self.row.pk,
                user_id=self.member.pk,
                organization_id=self.other_org.pk,
            )
        self.assertEqual(caught.exception.organization_ids, (self.org.pk,))
        self.row.refresh_from_db()
        self.assertEqual(self.row.organization_id, self.org.pk)
        self.assertEqual(self.row.user_id, self.owner.pk)
        self.assertTrue(self.owner.has_perm(_MANAGE, self.org))

    def test_stored_boundary_is_authorized_before_a_missing_replacement(self):
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: update_organization_ownership(
                self.other, self.row.pk, organization_id=_MISSING,
            ),
        )

    def test_in_memory_superuser_flag_does_not_remove_an_owner(self):
        second = add_organization_owner(
            self.owner, self.org.pk, self.member.pk,
        )
        self.other.is_superuser = True
        self.other.is_active = True
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: delete_organization_ownership(self.other, second.pk),
        )
        self.other.refresh_from_db()
        self.assertFalse(self.other.is_superuser)
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=second.pk).exists()
        )

    def test_inactive_actor_is_denied_before_the_inquiry(self):
        second = add_organization_owner(
            self.owner, self.org.pk, self.member.pk,
        )
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])

        def remove_second():
            delete_organization_ownership(self.owner, second.pk)

        with self.assertRaises(ManagementDenied):
            _assert_inquiry_not_called(self, remove_second)
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=second.pk).exists()
        )

        self.owner.is_active = True
        self.owner.save(update_fields=['is_active'])
        delete_organization_ownership(self.owner, second.pk)
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])

        def remove_last():
            delete_organization_ownership(self.owner, self.row.pk)

        with self.assertRaises(ManagementDenied):
            _assert_inquiry_not_called(self, remove_last)
        self.row.refresh_from_db()

        extra = OrganizationOwnership.objects.create(
            user=self.member, organization=self.org,
        )
        superuser = self._superuser()
        superuser.is_active = False
        superuser.save(update_fields=['is_active'])

        def superuser_removes_extra():
            delete_organization_ownership(superuser, extra.pk)

        with self.assertRaises(ManagementDenied):
            _assert_inquiry_not_called(self, superuser_removes_extra)
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=extra.pk).exists()
        )

    def test_active_superuser_can_fill_an_empty_organization_but_not_empty_it(self):
        bare = create_organization('bare')
        superuser = self._superuser()
        self.assertFalse(
            OrganizationOwnership.objects.filter(user=superuser).exists()
        )
        added = add_organization_owner(superuser, bare.pk, self.member.pk)
        self.assertTrue(self.member.has_perm(_MANAGE, bare))
        self.assertNotIn(
            bare,
            set(Organization.objects.authorized(superuser, self.manage)),
        )

        def remove_only():
            delete_organization_ownership(superuser, added.pk)

        with self.assertRaises(LastOrganizationOwner) as caught:
            _assert_inquiry_not_called(self, remove_only)
        self.assertEqual(caught.exception.organization_ids, (bare.pk,))
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=added.pk).exists()
        )

        def move_only():
            update_organization_ownership(
                superuser, added.pk, organization_id=self.org.pk,
            )

        with self.assertRaises(LastOrganizationOwner) as caught:
            _assert_inquiry_not_called(self, move_only)
        self.assertEqual(caught.exception.organization_ids, (bare.pk,))
        added.refresh_from_db()
        self.assertEqual(added.organization_id, bare.pk)

        updated = update_organization_ownership(
            superuser, added.pk, user_id=self.owner.pk,
        )
        self.assertEqual(updated.user_id, self.owner.pk)
        self.assertEqual(bare.ownerships.count(), 1)
        second = add_organization_owner(
            superuser, bare.pk, self.member.pk,
        )
        delete_organization_ownership(superuser, second.pk)
        self.assertEqual(
            set(bare.ownerships.values_list('user_id', flat=True)),
            {self.owner.pk},
        )
        self.assertFalse(
            OrganizationOwnership.objects.filter(user=superuser).exists()
        )
        self.assertNotIn(
            bare,
            set(Organization.objects.authorized(superuser, self.manage)),
        )

    def test_ownership_delete_locks_the_organization_before_the_inquiry(self):
        import gh_permissions.services as services

        second = add_organization_owner(
            self.owner, self.org.pk, self.member.pk,
        )
        events = []
        original_fetch = QuerySet._fetch_all
        original_atomic = services.transaction.atomic
        original_authorized = Organization.objects.authorized

        def fetch(queryset):
            if queryset.query.select_for_update:
                events.append(('lock', queryset.model))
            return original_fetch(queryset)

        class RecordingAtomic(object):
            def __init__(self, context):
                self.context = context

            def __enter__(self):
                events.append('atomic')
                return self.context.__enter__()

            def __exit__(self, exc_type, exc, tb):
                return self.context.__exit__(exc_type, exc, tb)

        def atomic(*args, **kwargs):
            return RecordingAtomic(original_atomic(*args, **kwargs))

        def authorized(self, user, permission, extra_q=None):
            events.append('inquiry')
            return original_authorized(user, permission, extra_q=extra_q)

        with patch.object(QuerySet, '_fetch_all', fetch):
            with patch.object(services.transaction, 'atomic', atomic):
                with patch.object(
                    type(Organization.objects), 'authorized', authorized,
                ):
                    delete_organization_ownership(self.owner, second.pk)
        lock_at = events.index(('lock', Organization))
        inquiry_at = events.index('inquiry')
        self.assertLess(events.index('atomic'), lock_at)
        self.assertLess(lock_at, inquiry_at)
        self.assertFalse(
            OrganizationOwnership.objects.filter(pk=second.pk).exists()
        )

    def test_undefined_and_duplicate_ownership_writes_do_not_mutate(self):
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: update_organization_ownership(self.owner, self.row.pk),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: update_organization_ownership(
                self.owner, True, user_id=self.member.pk,
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: delete_organization_ownership(self.owner, self.row),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: delete_organization_ownership(self.owner, _MISSING),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: update_organization_ownership(
                self.owner, self.row.pk, user_id=_MISSING,
            ),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: update_organization_ownership(
                self.owner, self.row.pk, organization_id=_MISSING,
            ),
        )
        second = add_organization_owner(
            self.owner, self.org.pk, self.member.pk,
        )
        _assert_no_mutation(
            self,
            DuplicateRelationshipTarget,
            lambda: update_organization_ownership(
                self.owner, self.row.pk, user_id=self.member.pk,
            ),
        )
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=second.pk).exists()
        )

    def test_personal_organization_ownership_is_not_the_conventional_rule(self):
        personal = OrganizationOwnership.objects.get(
            user=self.owner,
            organization=self.owner.personal_organization,
        )
        delete_organization_ownership(self.owner, personal.pk)
        self.owner.personal_organization.refresh_from_db()
        self.assertFalse(
            OrganizationOwnership.objects.filter(pk=personal.pk).exists()
        )
        self.assertFalse(
            self.owner.has_perm(_MANAGE, self.owner.personal_organization)
        )
        self.assertTrue(self.owner.has_perm(_MANAGE, self.org))

    def test_delete_user_reports_every_sole_conventional_organization(self):
        import gh_permissions.services as services

        user = create_user('departing')
        personal_pk = user.personal_organization.pk
        low = create_organization('low-org')
        mid = create_organization('mid-org')
        high = create_organization('high-org')
        self.assertLess(low.pk, mid.pk)
        self.assertLess(mid.pk, high.pk)
        OrganizationOwnership.objects.create(user=user, organization=high)
        OrganizationOwnership.objects.create(user=user, organization=low)
        OrganizationOwnership.objects.create(user=user, organization=mid)
        OrganizationOwnership.objects.create(user=self.owner, organization=mid)
        before = _graph()
        locked = []
        original = services._lock_organization

        def lock(pk):
            locked.append(pk)
            return original(pk)

        with CaptureQueriesContext(connection) as captured:
            with patch.object(services, '_lock_organization', lock):
                with self.assertRaises(LastOrganizationOwner) as caught:
                    delete_user(user)
        self.assertEqual(locked, [low.pk, mid.pk, high.pk])
        self.assertNotIn(personal_pk, locked)
        self.assertEqual(
            caught.exception.organization_ids, (low.pk, high.pk),
        )
        self.assertEqual(_graph(), before)
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertTrue(Alias.objects.filter(name='departing').exists())
        self.assertTrue(Organization.objects.filter(pk=personal_pk).exists())
        self.assertTrue(OrganizationOwnership.objects.filter(
            user=user, organization_id=personal_pk,
        ).exists())
        for query in captured.captured_queries:
            sql = query['sql'].lstrip().upper()
            self.assertFalse(
                sql.startswith(('INSERT', 'UPDATE', 'DELETE')),
                query['sql'],
            )

    def test_delete_user_keeps_a_co_owner_and_drops_the_personal_organization(self):
        solo = create_user('solo')
        solo_personal = solo.personal_organization.pk
        delete_user(solo)
        self.assertFalse(get_user_model().objects.filter(pk=solo.pk).exists())
        self.assertFalse(Organization.objects.filter(pk=solo_personal).exists())
        self.assertFalse(Alias.objects.filter(name='solo').exists())

        user = create_user('departing')
        personal_pk = user.personal_organization.pk
        shared = create_organization('shared')
        OrganizationOwnership.objects.create(user=user, organization=shared)
        OrganizationOwnership.objects.create(
            user=self.owner, organization=shared,
        )
        delete_user(user)
        self.assertFalse(
            get_user_model().objects.filter(username='departing').exists()
        )
        self.assertFalse(Alias.objects.filter(name='departing').exists())
        self.assertFalse(Organization.objects.filter(pk=personal_pk).exists())
        self.assertTrue(Organization.objects.filter(pk=shared.pk).exists())
        self.assertEqual(
            set(shared.ownerships.values_list('user_id', flat=True)),
            {self.owner.pk},
        )
        self.assertTrue(self.owner.has_perm(_MANAGE, shared))

    def test_delete_user_rolls_back_when_a_later_statement_fails(self):
        user = create_user('later')
        personal_pk = user.personal_organization.pk
        shared = create_organization('shared-later')
        OrganizationOwnership.objects.create(user=user, organization=shared)
        OrganizationOwnership.objects.create(
            user=self.owner, organization=shared,
        )
        User = get_user_model()
        with patch.object(User, 'delete', side_effect=RuntimeError('stop')):
            with self.assertRaises(RuntimeError):
                delete_user(user)
        user.refresh_from_db()
        self.assertEqual(user.username, 'later')
        self.assertTrue(Alias.objects.filter(name='later').exists())
        self.assertTrue(Organization.objects.filter(pk=personal_pk).exists())
        self.assertTrue(OrganizationOwnership.objects.filter(
            user=user, organization=shared,
        ).exists())
        self.assertTrue(OrganizationOwnership.objects.filter(
            user=user, organization_id=personal_pk,
        ).exists())

    def test_last_owner_decision_uses_the_post_lock_current_read(self):
        import gh_permissions.services as services

        user = create_user('departing')
        shared = create_organization('shared-lock')
        OrganizationOwnership.objects.create(user=user, organization=shared)
        co_owner = OrganizationOwnership.objects.create(
            user=self.owner, organization=shared,
        )
        refusal = self._assert_post_lock_current_read(
            lambda: delete_user(user),
            shared.pk,
            co_owner.pk,
        )
        self.assertEqual(refusal.organization_ids, (shared.pk,))
        self.assertTrue(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertTrue(Alias.objects.filter(name='departing').exists())
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=co_owner.pk).exists()
        )

        second = add_organization_owner(
            self.owner, self.org.pk, self.member.pk,
        )
        refusal = self._assert_post_lock_current_read(
            lambda: delete_organization_ownership(self.owner, self.row.pk),
            self.org.pk,
            second.pk,
        )
        self.assertEqual(refusal.organization_ids, (self.org.pk,))
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=self.row.pk).exists()
        )

        superuser = self._superuser('root-lock')
        refusal = self._assert_post_lock_current_read(
            lambda: update_organization_ownership(
                superuser, self.row.pk, organization_id=self.other_org.pk,
            ),
            self.org.pk,
            second.pk,
            only_locking=False,
        )
        self.assertEqual(refusal.organization_ids, (self.org.pk,))
        self.row.refresh_from_db()
        self.assertEqual(self.row.organization_id, self.org.pk)
        self.assertEqual(self.row.user_id, self.owner.pk)

    def _assert_post_lock_current_read(
        self, call, organization_id, removed_pk, only_locking=True,
    ):
        original_fetch = QuerySet._fetch_all
        events = []

        def fetch(queryset):
            locking = bool(queryset.query.select_for_update)
            result = original_fetch(queryset)
            events.append((queryset.model, locking))
            locked_ids = []
            if queryset.model is Organization and locking:
                locked_ids = [
                    row.pk for row in queryset._result_cache or ()
                ]
            if (
                locked_ids
                and organization_id in locked_ids
                and not fetch.removed
            ):
                fetch.removed = True
                with patch.object(QuerySet, '_fetch_all', original_fetch):
                    OrganizationOwnership.objects.filter(
                        pk=removed_pk,
                    ).delete()
            return result

        fetch.removed = False

        with patch.object(QuerySet, '_fetch_all', fetch):
            with self.assertRaises(LastOrganizationOwner) as caught:
                call()
        org_at = events.index((Organization, True))
        after = events[org_at + 1:]
        self.assertIn((OrganizationOwnership, True), after)
        if only_locking:
            self.assertNotIn((OrganizationOwnership, False), after)
        return caught.exception

    def test_raw_writes_and_register_condition_do_not_keep_an_owner(self):
        handle = isolated_handle()
        register_organization_owner(handle)
        self.assertGreaterEqual(len(handle.registry.records), 1)
        for record in handle.registry.records:
            self.assertIsNone(record.condition)

        self.org.owners.remove(self.owner)
        self.assertFalse(self.org.ownerships.exists())
        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.org,
        )
        OrganizationOwnership.objects.filter(
            user=self.owner, organization=self.org,
        ).delete()
        self.assertFalse(self.org.ownerships.exists())

        user = create_user('raw-user')
        raw_org = create_organization('raw-org')
        OrganizationOwnership.objects.create(user=user, organization=raw_org)
        user.delete()
        self.assertFalse(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=raw_org.pk).exists())
        self.assertFalse(raw_org.ownerships.exists())
        self.assertTrue(Alias.objects.filter(name='raw-user').exists())

    def test_team_and_repository_organization_moves_are_immutable(self):
        team = Team.objects.create(organization=self.org, name='writers')
        repository = Repository.objects.create(
            organization=self.org, name='app',
        )
        read = repository_permission('read_repository')
        grant = TeamRepositoryPermission.objects.create(
            team=team, repository=repository, operation=read,
        )
        superuser = self._superuser()
        calls = (
            (
                'Team',
                lambda actor: move_team_organization(
                    actor, team.pk, self.other_org.pk,
                ),
            ),
            (
                'Repository',
                lambda actor: move_repository_organization(
                    actor, repository.pk, self.other_org.pk,
                ),
            ),
        )
        for label, call in calls:
            for actor in (self.owner, superuser):
                before = _graph()
                with self.assertRaises(ImmutableOrganizationBoundary) as caught:
                    call(actor)
                self.assertEqual(caught.exception.label, label)
                self.assertEqual(_graph(), before)
        team.refresh_from_db()
        repository.refresh_from_db()
        self.assertEqual(team.organization_id, self.org.pk)
        self.assertEqual(repository.organization_id, self.org.pk)
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(pk=grant.pk).exists()
        )

        _assert_no_mutation(
            self,
            ImmutableOrganizationBoundary,
            lambda: move_team_organization(
                self.owner, team.pk, self.org.pk,
            ),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: move_repository_organization(
                self.owner, _MISSING, self.org.pk,
            ),
        )
        _assert_no_mutation(
            self,
            MissingRelationshipTarget,
            lambda: move_team_organization(
                self.owner, team.pk, _MISSING,
            ),
        )
        _assert_no_mutation(
            self,
            UndefinedRelationshipWrite,
            lambda: move_repository_organization(
                self.owner, repository.pk, repository,
            ),
        )

        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: move_team_organization(
                self.owner, team.pk, self.other_org.pk,
            ),
        )
        superuser.is_active = False
        superuser.save(update_fields=['is_active'])
        _assert_no_mutation(
            self,
            ManagementDenied,
            lambda: move_repository_organization(
                superuser, repository.pk, self.other_org.pk,
            ),
        )

        Team.objects.filter(pk=team.pk).update(organization=self.other_org)
        team.refresh_from_db()
        self.assertEqual(team.organization_id, self.other_org.pk)
        grant.refresh_from_db()
        with self.assertRaises(ValidationError):
            grant.clean()
