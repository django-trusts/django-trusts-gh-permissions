"""Stock admin mutations go through the relationship services.

Request-level coverage for the admin slice of issue #28: an owner, an
owner of another organization, an inactive actor, an active superuser,
a forged parent id, a refused write that leaves the previous graph,
and the built-in bulk delete action. ``OrganizationOwnership`` stays
closed to non-superusers. Superuser ownership writes, team and
repository edits, collaborator and team-grant edits, and user deletion
call the services. ``LastOrganizationOwner`` is a message.
"""

from unittest.mock import patch

from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.admin.options import ModelAdmin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase
from django.urls import reverse

from gh_permissions import admin as admin_module

from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import create_organization, create_user
from tests.fixtures import repository_permission


MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


def _grant_scoped(user):
    codenames = []
    for model in (
        Organization, Team, Repository, RepositoryCollaborator,
        TeamRepositoryPermission, OrganizationOwnership,
    ):
        for action in ('view', 'add', 'change', 'delete'):
            codenames.append('%s_%s' % (action, model._meta.model_name))
    user.user_permissions.set(Permission.objects.filter(
        content_type__app_label='gh_permissions',
        codename__in=codenames,
    ))


def _client_for(user):
    client = Client()
    client.force_login(user, backend=MODEL_BACKEND)
    return client


class AdminRelationshipServiceTests(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.owner = create_user('ada-owner', is_staff=True)
        self.second = create_user('bea-owner', is_staff=True)
        self.other = create_user('foreign-owner', is_staff=True)
        self.target = create_user('grant-target')
        self.spare = create_user('spare-user')
        self.inactive = create_user('idle-owner', is_staff=True)
        self.inactive.is_active = False
        self.inactive.save(update_fields=['is_active'])
        self.inactive_super = User.objects.create_superuser(
            username='idle-root',
            email='idle-root@example.com',
            password='secret',
        )
        self.inactive_super.is_active = False
        self.inactive_super.save(update_fields=['is_active'])
        self.root = User.objects.create_superuser(
            username='root', email='root@example.com', password='secret',
        )
        for user in (self.owner, self.second, self.other, self.inactive):
            _grant_scoped(user)
        self.org = create_organization('acme')
        self.shared = create_organization('shared')
        self.foreign = create_organization('foreign')
        self.bare = create_organization('bare')
        self.owner_row = OrganizationOwnership.objects.create(
            user=self.owner, organization=self.org,
        )
        self.shared_owner_row = OrganizationOwnership.objects.create(
            user=self.owner, organization=self.shared,
        )
        self.shared_second_row = OrganizationOwnership.objects.create(
            user=self.second, organization=self.shared,
        )
        OrganizationOwnership.objects.create(
            user=self.other, organization=self.foreign,
        )
        self.team = Team.objects.create(organization=self.org, name='writers')
        self.team.members.add(self.target)
        self.read = repository_permission('read_repository')
        self.write = repository_permission('write_repository')
        self.team.allowed_operations.add(self.read)
        self.repo = Repository.objects.create(
            organization=self.org, name='alpha',
        )
        self.repo_shared = Repository.objects.create(
            organization=self.shared, name='shared-repo',
        )
        self.repo_foreign = Repository.objects.create(
            organization=self.foreign, name='foreign-repo',
        )
        self.foreign_team = Team.objects.create(
            organization=self.foreign, name='outsiders',
        )
        self.owner_client = _client_for(self.owner)
        self.other_client = _client_for(self.other)
        self.root_client = _client_for(self.root)
        self.inactive_client = _client_for(self.inactive)
        self.inactive_super_client = _client_for(self.inactive_super)

    def _team_post(self, client, team, **overrides):
        data = {
            'name': team.name,
            'organization': str(team.organization_id),
            'members': [str(self.target.pk)],
            'allowed_operations': [str(self.read.pk)],
            '_save': 'Save',
        }
        data.update(overrides)
        return client.post(
            reverse('admin:gh_permissions_team_change', args=[team.pk]),
            data,
        )

    def test_owner_edits_grants_and_cannot_move_organization(self):
        renamed = self._team_post(
            self.owner_client, self.team, name='writers-renamed',
        )
        self.assertEqual(renamed.status_code, 302, renamed.content[:500])
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'writers-renamed')
        self.assertEqual(self.team.organization_id, self.org.pk)
        self.assertEqual(
            set(self.team.members.values_list('pk', flat=True)),
            {self.target.pk},
        )

        refused = self._team_post(
            self.owner_client,
            self.team,
            name='should-not-stick',
            organization=str(self.shared.pk),
            members=[str(self.second.pk)],
            allowed_operations=[str(self.write.pk)],
        )
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, 'Team.organization cannot be moved.')
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'writers-renamed')
        self.assertEqual(self.team.organization_id, self.org.pk)
        self.assertEqual(
            set(self.team.members.values_list('pk', flat=True)),
            {self.target.pk},
        )
        self.assertEqual(
            set(self.team.allowed_operations.values_list('pk', flat=True)),
            {self.read.pk},
        )
        self.assertFalse(self.second.has_perm(
            'gh_permissions.write_repository', self.repo,
        ))

        repo_renamed = self.owner_client.post(
            reverse('admin:gh_permissions_repository_change', args=[self.repo.pk]),
            {
                'name': 'alpha-renamed',
                'organization': str(self.org.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(repo_renamed.status_code, 302, repo_renamed.content[:500])
        self.repo.refresh_from_db()
        self.assertEqual(self.repo.name, 'alpha-renamed')
        repo_refused = self.owner_client.post(
            reverse('admin:gh_permissions_repository_change', args=[self.repo.pk]),
            {
                'name': 'should-not-stick',
                'organization': str(self.shared.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(repo_refused.status_code, 200)
        self.assertContains(
            repo_refused, 'Repository.organization cannot be moved.',
        )
        self.repo.refresh_from_db()
        self.assertEqual(self.repo.name, 'alpha-renamed')
        self.assertEqual(self.repo.organization_id, self.org.pk)

        forged = self.owner_client.post(
            reverse('admin:gh_permissions_repositorycollaborator_add'),
            {
                'user': str(self.target.pk),
                'repository': str(self.repo_foreign.pk),
                'permissions': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(forged.status_code, 200)
        self.assertContains(forged, 'valid choice')
        self.assertEqual(RepositoryCollaborator.objects.count(), 0)

        created = self.owner_client.post(
            reverse('admin:gh_permissions_repositorycollaborator_add'),
            {
                'user': str(self.target.pk),
                'repository': str(self.repo.pk),
                'permissions': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(created.status_code, 302, created.content[:500])
        direct = RepositoryCollaborator.objects.get()
        self.assertTrue(self.target.has_perm(
            'gh_permissions.read_repository', self.repo,
        ))
        updated = self.owner_client.post(
            reverse(
                'admin:gh_permissions_repositorycollaborator_change',
                args=[direct.pk],
            ),
            {
                'user': str(self.target.pk),
                'repository': str(self.repo_shared.pk),
                'permissions': [str(self.write.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(updated.status_code, 302, updated.content[:500])
        direct.refresh_from_db()
        self.assertEqual(direct.repository_id, self.repo_shared.pk)
        self.assertEqual(
            set(direct.permissions.values_list('pk', flat=True)),
            {self.write.pk},
        )
        self.assertFalse(self.target.has_perm(
            'gh_permissions.read_repository', self.repo,
        ))
        self.assertTrue(self.target.has_perm(
            'gh_permissions.write_repository', self.repo_shared,
        ))

        duplicate = self.owner_client.post(
            reverse('admin:gh_permissions_repositorycollaborator_add'),
            {
                'user': str(self.target.pk),
                'repository': str(self.repo_shared.pk),
                'permissions': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertIn(duplicate.status_code, (200, 302))
        self.assertEqual(RepositoryCollaborator.objects.count(), 1)
        direct.refresh_from_db()
        self.assertEqual(
            set(direct.permissions.values_list('pk', flat=True)),
            {self.write.pk},
        )

        grant = self.owner_client.post(
            reverse('admin:gh_permissions_teamrepositorypermission_add'),
            {
                'team': str(self.team.pk),
                'repository': str(self.repo.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(grant.status_code, 302, grant.content[:500])
        self.assertTrue(self.target.has_perm(
            'gh_permissions.read_repository', self.repo,
        ))
        misaligned = self.owner_client.post(
            reverse('admin:gh_permissions_teamrepositorypermission_add'),
            {
                'team': str(self.team.pk),
                'repository': str(self.repo_foreign.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(misaligned.status_code, 200)
        self.assertContains(misaligned, 'valid choice')
        self.assertEqual(TeamRepositoryPermission.objects.count(), 1)

    def test_other_tenant_owner_and_forged_ids_do_not_write(self):
        before_members = set(self.team.members.values_list('pk', flat=True))
        denied = self.other_client.post(
            reverse('admin:gh_permissions_team_change', args=[self.team.pk]),
            {
                'name': 'hijack',
                'organization': str(self.foreign.pk),
                'members': [str(self.other.pk)],
                'allowed_operations': [str(self.write.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied['Location'], reverse('admin:index'))
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'writers')
        self.assertEqual(
            set(self.team.members.values_list('pk', flat=True)),
            before_members,
        )
        forged_team = self.owner_client.post(
            reverse('admin:gh_permissions_team_add'),
            {
                'name': 'smuggled',
                'organization': str(self.foreign.pk),
                'members': [str(self.target.pk)],
                'allowed_operations': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(forged_team.status_code, 200)
        self.assertContains(forged_team, 'valid choice')
        self.assertFalse(Team.objects.filter(name='smuggled').exists())
        missing = self.root_client.post(
            reverse('admin:gh_permissions_repositorycollaborator_add'),
            {
                'user': '999999',
                'repository': str(self.repo.pk),
                'permissions': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(missing.status_code, 200)
        self.assertContains(missing, 'valid choice')
        self.assertEqual(RepositoryCollaborator.objects.count(), 0)
        self.root_client.post(
            reverse('admin:gh_permissions_teamrepositorypermission_add'),
            {
                'team': str(self.team.pk),
                'repository': str(self.repo_foreign.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(TeamRepositoryPermission.objects.count(), 0)

    def test_inactive_actors_are_refused_before_a_write(self):
        before = (
            self.team.name,
            self.team.organization_id,
            set(self.team.members.values_list('pk', flat=True)),
        )
        for client in (self.inactive_client, self.inactive_super_client):
            response = self._team_post(
                client, self.team, name='inactive-rename',
            )
            self.assertEqual(response.status_code, 302)
            self.assertIn('/admin/login/', response['Location'])
        self.team.refresh_from_db()
        self.assertEqual(
            (
                self.team.name,
                self.team.organization_id,
                set(self.team.members.values_list('pk', flat=True)),
            ),
            before,
        )
        user_delete = self.inactive_super_client.post(
            reverse('admin:example_user_delete', args=[self.spare.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(user_delete.status_code, 302)
        self.assertIn('/admin/login/', user_delete['Location'])
        self.assertTrue(
            get_user_model().objects.filter(pk=self.spare.pk).exists(),
        )

    def test_active_superuser_bootstraps_and_cannot_move_or_empty(self):
        added = self.root_client.post(
            reverse('admin:gh_permissions_organizationownership_add'),
            {
                'organization': str(self.bare.pk),
                'user': str(self.second.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(added.status_code, 302, added.content[:500])
        self.assertTrue(self.second.has_perm(
            'gh_permissions.manage_organization', self.bare,
        ))
        moved = self._team_post(
            self.root_client,
            self.team,
            name='root-rename',
            organization=str(self.foreign.pk),
            members=[str(self.other.pk)],
        )
        self.assertEqual(moved.status_code, 200)
        self.assertContains(moved, 'Team.organization cannot be moved.')
        self.team.refresh_from_db()
        self.assertEqual(self.team.name, 'writers')
        self.assertEqual(self.team.organization_id, self.org.pk)
        self.assertNotIn(self.other, self.team.members.all())
        repo_moved = self.root_client.post(
            reverse(
                'admin:gh_permissions_repository_change', args=[self.repo.pk],
            ),
            {
                'name': 'root-repo',
                'organization': str(self.foreign.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(repo_moved.status_code, 200)
        self.assertContains(
            repo_moved, 'Repository.organization cannot be moved.',
        )
        self.repo.refresh_from_db()
        self.assertEqual(self.repo.name, 'alpha')
        self.assertEqual(self.repo.organization_id, self.org.pk)

        removed = self.root_client.post(
            reverse(
                'admin:gh_permissions_organizationownership_delete',
                args=[self.shared_second_row.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(removed.status_code, 302, removed.content[:500])
        self.assertFalse(
            OrganizationOwnership.objects.filter(
                pk=self.shared_second_row.pk,
            ).exists(),
        )
        self.assertTrue(self.owner.has_perm(
            'gh_permissions.manage_organization', self.shared,
        ))

        refused = self.root_client.post(
            reverse(
                'admin:gh_permissions_organizationownership_delete',
                args=[self.owner_row.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(refused.status_code, 302, refused.content[:500])
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=self.owner_row.pk).exists(),
        )
        self.assertContains(
            self.root_client.get(refused['Location']),
            'must keep an owner',
        )
        retarget = self.root_client.post(
            reverse(
                'admin:gh_permissions_organizationownership_change',
                args=[self.owner_row.pk],
            ),
            {
                'user': str(self.owner.pk),
                'organization': str(self.foreign.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(retarget.status_code, 302, retarget.content[:500])
        self.owner_row.refresh_from_db()
        self.assertEqual(self.owner_row.organization_id, self.org.pk)
        self.assertEqual(self.owner_row.user_id, self.owner.pk)
        self.assertContains(
            self.root_client.get(retarget['Location']),
            'must keep an owner',
        )

    def test_refused_bulk_and_user_deletion_persist_nothing(self):
        bulk = self.root_client.post(
            reverse('admin:gh_permissions_organizationownership_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '0',
                ACTION_CHECKBOX_NAME: [
                    str(self.owner_row.pk),
                    str(self.shared_second_row.pk),
                ],
            },
        )
        self.assertEqual(bulk.status_code, 302, bulk.content[:500])
        self.assertContains(
            self.root_client.get(bulk['Location']),
            'must keep an owner',
        )
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=self.owner_row.pk).exists(),
        )
        self.assertTrue(
            OrganizationOwnership.objects.filter(
                pk=self.shared_second_row.pk,
            ).exists(),
        )
        self.assertNotContains(
            self.root_client.get(
                reverse('admin:gh_permissions_organizationownership_changelist'),
            ),
            'Successfully deleted',
        )

        user_delete = self.root_client.post(
            reverse('admin:example_user_delete', args=[self.owner.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(user_delete.status_code, 302, user_delete.content[:500])
        personal_org = Organization.objects.get(personal_user=self.owner)
        self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())
        self.assertTrue(Alias.objects.filter(name='ada-owner').exists())
        self.assertTrue(Organization.objects.filter(pk=personal_org.pk).exists())
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=self.owner_row.pk).exists(),
        )
        self.assertContains(
            self.root_client.get(user_delete['Location']),
            'must keep an owner',
        )

        personal = self.root_client.post(
            reverse(
                'admin:gh_permissions_organization_delete',
                args=[personal_org.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(personal.status_code, 302, personal.content[:500])
        self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=personal_org.pk).exists())
        self.assertContains(
            self.root_client.get(personal['Location']),
            'must keep an owner',
        )

        users = self.root_client.post(
            reverse('admin:example_user_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '0',
                ACTION_CHECKBOX_NAME: [
                    str(self.spare.pk),
                    str(self.owner.pk),
                ],
            },
        )
        self.assertEqual(users.status_code, 302, users.content[:500])
        self.assertTrue(get_user_model().objects.filter(pk=self.spare.pk).exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.owner.pk).exists())
        self.assertTrue(Alias.objects.filter(name='spare-user').exists())
        self.assertContains(
            self.root_client.get(users['Location']),
            'must keep an owner',
        )

    def test_owner_bulk_delete_uses_the_collaborator_and_team_services(self):
        direct = RepositoryCollaborator.objects.create(
            user=self.target, repository=self.repo,
        )
        direct.permissions.add(self.read)
        other_direct = RepositoryCollaborator.objects.create(
            user=self.target, repository=self.repo_foreign,
        )
        other_direct.permissions.add(self.write)
        kept = Team.objects.create(organization=self.org, name='keepers')
        removed = self.owner_client.post(
            reverse('admin:gh_permissions_repositorycollaborator_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '0',
                ACTION_CHECKBOX_NAME: [
                    str(direct.pk),
                    str(other_direct.pk),
                ],
            },
        )
        self.assertEqual(removed.status_code, 302, removed.content[:500])
        self.assertFalse(
            RepositoryCollaborator.objects.filter(pk=direct.pk).exists(),
        )
        self.assertTrue(
            RepositoryCollaborator.objects.filter(pk=other_direct.pk).exists(),
        )
        self.assertFalse(self.target.has_perm(
            'gh_permissions.read_repository', self.repo,
        ))
        self.assertTrue(self.target.has_perm(
            'gh_permissions.write_repository', self.repo_foreign,
        ))
        TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.repo, operation=self.read,
        )
        self.assertTrue(self.target.has_perm(
            'gh_permissions.read_repository', self.repo,
        ))
        bulk_teams = self.owner_client.post(
            reverse('admin:gh_permissions_team_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '0',
                ACTION_CHECKBOX_NAME: [
                    str(self.team.pk),
                    str(self.foreign_team.pk),
                ],
            },
        )
        self.assertEqual(bulk_teams.status_code, 302, bulk_teams.content[:500])
        self.assertFalse(Team.objects.filter(pk=self.team.pk).exists())
        self.assertFalse(
            TeamRepositoryPermission.objects.filter(team_id=self.team.pk).exists(),
        )
        self.assertTrue(Team.objects.filter(pk=self.foreign_team.pk).exists())
        self.assertTrue(Team.objects.filter(pk=kept.pk).exists())
        self.assertFalse(self.target.has_perm(
            'gh_permissions.read_repository', self.repo,
        ))

    def test_owner_creates_and_bulk_deletes_repositories(self):
        created = self.owner_client.post(
            reverse('admin:gh_permissions_repository_add'),
            {
                'name': 'gamma',
                'organization': str(self.org.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(created.status_code, 302, created.content[:500])
        gamma = Repository.objects.get(name='gamma')
        self.assertEqual(gamma.organization_id, self.org.pk)
        kept = Repository.objects.create(organization=self.org, name='kept')
        kept_direct = RepositoryCollaborator.objects.create(
            user=self.target, repository=kept,
        )
        kept_grant = TeamRepositoryPermission.objects.create(
            team=self.team, repository=kept, operation=self.read,
        )
        foreign_direct = RepositoryCollaborator.objects.create(
            user=self.target, repository=self.repo_foreign,
        )
        foreign_grant = TeamRepositoryPermission.objects.create(
            team=self.foreign_team,
            repository=self.repo_foreign,
            operation=self.write,
        )
        gamma_direct = RepositoryCollaborator.objects.create(
            user=self.target, repository=gamma,
        )
        gamma_grant = TeamRepositoryPermission.objects.create(
            team=self.team, repository=gamma, operation=self.read,
        )
        bulk = self.owner_client.post(
            reverse('admin:gh_permissions_repository_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '0',
                ACTION_CHECKBOX_NAME: [
                    str(gamma.pk),
                    str(self.repo.pk),
                    str(self.repo_foreign.pk),
                ],
            },
        )
        self.assertEqual(bulk.status_code, 302, bulk.content[:500])
        self.assertFalse(Repository.objects.filter(pk=gamma.pk).exists())
        self.assertFalse(Repository.objects.filter(pk=self.repo.pk).exists())
        self.assertFalse(
            RepositoryCollaborator.objects.filter(pk=gamma_direct.pk).exists(),
        )
        self.assertFalse(
            TeamRepositoryPermission.objects.filter(pk=gamma_grant.pk).exists(),
        )
        self.assertTrue(Repository.objects.filter(pk=kept.pk).exists())
        self.assertTrue(
            RepositoryCollaborator.objects.filter(pk=kept_direct.pk).exists(),
        )
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(pk=kept_grant.pk).exists(),
        )
        self.assertTrue(
            Repository.objects.filter(pk=self.repo_foreign.pk).exists(),
        )
        self.assertTrue(
            RepositoryCollaborator.objects.filter(pk=foreign_direct.pk).exists(),
        )
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(pk=foreign_grant.pk).exists(),
        )

    def test_denied_and_forged_repository_writes_do_not_mutate(self):
        direct = RepositoryCollaborator.objects.create(
            user=self.target, repository=self.repo,
        )
        direct.permissions.add(self.read)
        grant = TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.repo, operation=self.read,
        )
        foreign_direct = RepositoryCollaborator.objects.create(
            user=self.target, repository=self.repo_foreign,
        )
        foreign_grant = TeamRepositoryPermission.objects.create(
            team=self.foreign_team,
            repository=self.repo_foreign,
            operation=self.write,
        )
        before = set(Repository.objects.values_list('pk', 'name', 'organization_id'))
        denied_create = self.other_client.post(
            reverse('admin:gh_permissions_repository_add'),
            {
                'name': 'stolen',
                'organization': str(self.org.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(denied_create.status_code, 200)
        self.assertContains(denied_create, 'valid choice')
        forged_create = self.owner_client.post(
            reverse('admin:gh_permissions_repository_add'),
            {
                'name': 'ghost',
                'organization': '999999',
                '_save': 'Save',
            },
        )
        self.assertEqual(forged_create.status_code, 200)
        self.assertContains(forged_create, 'valid choice')
        forged_foreign = self.owner_client.post(
            reverse('admin:gh_permissions_repository_add'),
            {
                'name': 'smuggled-repo',
                'organization': str(self.foreign.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(forged_foreign.status_code, 200)
        self.assertContains(forged_foreign, 'valid choice')
        for client in (self.inactive_client, self.inactive_super_client):
            inactive = client.post(
                reverse('admin:gh_permissions_repository_add'),
                {
                    'name': 'idle-repo',
                    'organization': str(self.org.pk),
                    '_save': 'Save',
                },
            )
            self.assertEqual(inactive.status_code, 302)
            self.assertIn('/admin/login/', inactive['Location'])
        self.assertEqual(
            set(Repository.objects.values_list('pk', 'name', 'organization_id')),
            before,
        )
        self.assertFalse(Repository.objects.filter(name='stolen').exists())
        self.assertFalse(Repository.objects.filter(name='ghost').exists())
        self.assertFalse(Repository.objects.filter(name='smuggled-repo').exists())
        self.assertFalse(Repository.objects.filter(name='idle-repo').exists())

        denied_delete = self.other_client.post(
            reverse('admin:gh_permissions_repository_delete', args=[self.repo.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(denied_delete.status_code, 302)
        self.assertEqual(denied_delete['Location'], reverse('admin:index'))
        forged_delete = self.owner_client.post(
            reverse('admin:gh_permissions_repository_delete', args=[999999]),
            {'post': 'yes'},
        )
        self.assertEqual(forged_delete.status_code, 302)
        self.assertEqual(forged_delete['Location'], reverse('admin:index'))
        forged_foreign_delete = self.owner_client.post(
            reverse(
                'admin:gh_permissions_repository_delete',
                args=[self.repo_foreign.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(forged_foreign_delete.status_code, 302)
        self.assertEqual(
            forged_foreign_delete['Location'], reverse('admin:index'),
        )
        for client in (self.inactive_client, self.inactive_super_client):
            inactive_delete = client.post(
                reverse(
                    'admin:gh_permissions_repository_delete',
                    args=[self.repo.pk],
                ),
                {'post': 'yes'},
            )
            self.assertEqual(inactive_delete.status_code, 302)
            self.assertIn('/admin/login/', inactive_delete['Location'])
        self.repo.refresh_from_db()
        self.repo_foreign.refresh_from_db()
        self.assertTrue(
            RepositoryCollaborator.objects.filter(pk=direct.pk).exists(),
        )
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(pk=grant.pk).exists(),
        )
        self.assertTrue(
            RepositoryCollaborator.objects.filter(pk=foreign_direct.pk).exists(),
        )
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(pk=foreign_grant.pk).exists(),
        )
        self.assertEqual(
            set(direct.permissions.values_list('pk', flat=True)),
            {self.read.pk},
        )

    def test_repository_write_refuses_when_ownership_disappears_after_preflight(self):
        """Ownership can disappear after the admin preflight.

        The scope check and the form choice do not lock. This removes
        the ownership row after that check and before the service
        returns. ``create_repository`` and ``delete_repository`` reload
        the organization inside the mutation transaction and refuse, so
        the create does not insert and the delete does not cascade.
        """
        direct = RepositoryCollaborator.objects.create(
            user=self.target, repository=self.repo,
        )
        direct.permissions.add(self.read)
        grant = TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.repo, operation=self.read,
        )
        original_guard = admin_module._guard_scope

        def guard_then_drop(model_admin, request, obj, change):
            original_guard(model_admin, request, obj, change)
            if not change and getattr(obj, 'name', None) == 'racy':
                OrganizationOwnership.objects.filter(
                    user=self.owner, organization=self.org,
                ).delete()

        with patch.object(admin_module, '_guard_scope', guard_then_drop):
            refused = self.owner_client.post(
                reverse('admin:gh_permissions_repository_add'),
                {
                    'name': 'racy',
                    'organization': str(self.org.pk),
                    '_save': 'Save',
                },
            )
        self.assertEqual(refused.status_code, 302, refused.content[:500])
        self.assertContains(
            self.owner_client.get(refused['Location']),
            'was denied for organization',
        )
        self.assertFalse(Repository.objects.filter(name='racy').exists())
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=self.owner_row.pk).exists(),
        )

        original_log = ModelAdmin.log_deletions

        def log_then_drop(self_admin, request, queryset):
            result = original_log(self_admin, request, queryset)
            if any(getattr(row, 'pk', None) == self.repo.pk for row in queryset):
                OrganizationOwnership.objects.filter(
                    user=self.owner, organization=self.org,
                ).delete()
            return result

        with patch.object(ModelAdmin, 'log_deletions', log_then_drop):
            refused_delete = self.owner_client.post(
                reverse(
                    'admin:gh_permissions_repository_delete',
                    args=[self.repo.pk],
                ),
                {'post': 'yes'},
            )
        self.assertEqual(
            refused_delete.status_code, 302, refused_delete.content[:500],
        )
        self.assertContains(
            self.owner_client.get(refused_delete['Location']),
            'was denied for organization',
        )
        self.repo.refresh_from_db()
        self.assertTrue(
            RepositoryCollaborator.objects.filter(pk=direct.pk).exists(),
        )
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(pk=grant.pk).exists(),
        )
        self.assertEqual(
            set(direct.permissions.values_list('pk', flat=True)),
            {self.read.pk},
        )
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=self.owner_row.pk).exists(),
        )

    def test_non_superuser_still_cannot_edit_ownership_rows(self):
        added = self.owner_client.post(
            reverse('admin:gh_permissions_organizationownership_add'),
            {
                'organization': str(self.org.pk),
                'user': str(self.target.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(added.status_code, 403)
        self.assertFalse(
            OrganizationOwnership.objects.filter(
                user=self.target, organization=self.org,
            ).exists(),
        )
        changed = self.owner_client.post(
            reverse(
                'admin:gh_permissions_organizationownership_change',
                args=[self.owner_row.pk],
            ),
            {
                'organization': str(self.shared.pk),
                'user': str(self.target.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(changed.status_code, 403)
        self.owner_row.refresh_from_db()
        self.assertEqual(self.owner_row.user_id, self.owner.pk)
        self.assertEqual(self.owner_row.organization_id, self.org.pk)
