"""Issue #28 stage 8: authorization-bearing relationship writes.

Domain services are tested without admin. The inquiry is
``manage_organization`` on the organization row locked inside the
write. A submitted instance, a new ownership row, and a permission
bundle are not that inquiry.
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
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import (
    CrossOrganizationRelationship,
    DuplicateRelationshipTarget,
    ManagementDenied,
    MissingRelationshipTarget,
    RelationshipWriteError,
    UndefinedRelationshipWrite,
    add_organization_owner,
    create_organization,
    create_repository_collaborator,
    create_team_repository_permission,
    create_user,
    delete_repository_collaborator,
    delete_team,
    delete_team_repository_permission,
    replace_collaborator_permissions,
    replace_team_members_and_ceiling,
    update_repository_collaborator,
    update_team_repository_permission,
)
from tests.fixtures import repository_permission


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

    def test_admin_is_not_wired_to_the_relationship_services(self):
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
        ):
            self.assertNotIn(name, source)
        self.assertNotIn('delete_team(', source)
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
