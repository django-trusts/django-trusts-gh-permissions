"""Seeded owner admin path against the runnable example settings.

``tests.settings`` lists ``GhAuthorizationBackend`` and ``ModelBackend``.
This module does not replace ``AUTHENTICATION_BACKENDS``. The owner
signs in through the admin login form.

``ModelBackend`` supplies password checks and no-object model
permissions. Object and organization scope stay on
``GhAuthorizationBackend`` and the admin adapter. Forged foreign
parents are rejected by that scoped form before a write.

Relationship writes in admin call ``gh_permissions.services``. This
proof stops at admin entry and the queryset boundary: the seeded
owner sees only owned rows, and a forged foreign parent does not
write.
"""

from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from example.management.commands.seed_example import (
    DEFAULT_ORGANIZATION_NAME,
    DEVELOPMENT_PASSWORDS,
    DIRECT_USERNAME,
    OUTSIDER_USERNAME,
    OWNER_USERNAME,
    REPOSITORY_TITLE,
    SUPERUSER_USERNAME,
    TEAM_NAME,
    TEAM_USERNAME,
)
from gh_permissions.models import (
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import create_organization, create_user


ROOT = Path(__file__).resolve().parents[1]
RUNNABLE_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
    'django.contrib.auth.backends.ModelBackend',
)
RUNNABLE_BACKENDS_TEXT = (
    "AUTHENTICATION_BACKENDS = (\n"
    "    'gh_permissions.backends.GhAuthorizationBackend',\n"
    "    'django.contrib.auth.backends.ModelBackend',\n"
    ")"
)
DENIAL = 'You don\u2019t have permission to view or edit anything.'
CHANGELISTS = (
    'admin:gh_permissions_organization_changelist',
    'admin:gh_permissions_team_changelist',
    'admin:gh_permissions_repository_changelist',
    'admin:gh_permissions_repositorycollaborator_changelist',
    'admin:gh_permissions_teamrepositorypermission_changelist',
    'admin:gh_permissions_organizationownership_changelist',
)


def _permission(model, codename):
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model=model,
        codename=codename,
    )


def _listed_pks(response):
    return set(response.context['cl'].queryset.values_list('pk', flat=True))


def _domain_writes(captured):
    writes = []
    for query in captured:
        sql = ' '.join(query['sql'].split())
        upper = sql.upper()
        if (
            upper.startswith(('INSERT', 'UPDATE', 'DELETE', 'REPLACE'))
            and 'gh_permissions_' in sql
        ):
            writes.append(sql)
    return writes


def _graph_signature():
    teams = []
    for team in Team.objects.order_by('pk'):
        teams.append((
            team.pk,
            team.name,
            team.organization_id,
            tuple(team.members.order_by('pk').values_list('pk', flat=True)),
        ))
    collaborators = []
    for row in RepositoryCollaborator.objects.order_by('pk'):
        collaborators.append((
            row.pk,
            row.user_id,
            row.repository_id,
            tuple(row.permissions.order_by('pk').values_list('pk', flat=True)),
        ))
    return {
        'organizations': list(
            Organization.objects.order_by('pk').values_list(
                'pk', 'name', 'personal_user_id',
            )
        ),
        'teams': teams,
        'repositories': list(
            Repository.objects.order_by('pk').values_list(
                'pk', 'name', 'organization_id',
            )
        ),
        'collaborators': collaborators,
        'grants': list(
            TeamRepositoryPermission.objects.order_by('pk').values_list(
                'pk', 'team_id', 'repository_id', 'operation_id',
            )
        ),
        'ownerships': list(
            OrganizationOwnership.objects.order_by('pk').values_list(
                'pk', 'user_id', 'organization_id',
            )
        ),
    }


class RunnableAdminSettingsTests(SimpleTestCase):
    def test_runnable_settings_list_both_layers_without_a_test_override(self):
        self.assertEqual(settings.AUTHENTICATION_BACKENDS, RUNNABLE_BACKENDS)
        settings_text = (ROOT / 'tests' / 'settings.py').read_text()
        self.assertIn(RUNNABLE_BACKENDS_TEXT, settings_text)
        for relative in (
            'README.md',
            'DEV.md',
            'docs/org-scoped-admin.md',
        ):
            self.assertIn(RUNNABLE_BACKENDS_TEXT, (ROOT / relative).read_text())
        proof = Path(__file__).read_text()
        hidden_override = 'override_' + 'settings(AUTHENTICATION_BACKENDS'
        self.assertNotIn('force_' + 'login', proof)
        self.assertNotIn(hidden_override, proof)
        for relative in (
            'tests/test_org_scoped_admin.py',
            'tests/test_org_scoped_requests.py',
            'tests/test_seed_example.py',
        ):
            self.assertNotIn(hidden_override, (ROOT / relative).read_text())


class SeededOwnerAdminPathTests(TestCase):
    def setUp(self):
        super().setUp()
        call_command('seed_example', stdout=StringIO())
        User = get_user_model()
        self.owner = User.objects.get(username=OWNER_USERNAME)
        self.direct = User.objects.get(username=DIRECT_USERNAME)
        self.team_user = User.objects.get(username=TEAM_USERNAME)
        self.outsider = User.objects.get(username=OUTSIDER_USERNAME)
        self.superuser = User.objects.get(username=SUPERUSER_USERNAME)
        self.organization = Organization.objects.get(name=DEFAULT_ORGANIZATION_NAME)
        self.repository = Repository.objects.get(
            organization=self.organization, name=REPOSITORY_TITLE,
        )
        self.team = Team.objects.get(
            organization=self.organization, name=TEAM_NAME,
        )
        self.collaboration = RepositoryCollaborator.objects.get(
            user=self.direct, repository=self.repository,
        )
        self.grants = set(
            TeamRepositoryPermission.objects.filter(
                team=self.team, repository=self.repository,
            )
        )
        self.read = _permission('repository', 'read_repository')
        self.write = _permission('repository', 'write_repository')
        self.manage = _permission('organization', 'manage_organization')
        self.foreign = create_organization('Outside Seed')
        self.foreign_owner = create_user(
            'foreign-owner', password='foreign-owner-dev-only', is_staff=True,
        )
        self.foreign_ownership = OrganizationOwnership.objects.create(
            user=self.foreign_owner, organization=self.foreign,
        )
        self.foreign_team = Team.objects.create(
            organization=self.foreign, name='outside-team',
        )
        self.foreign_team.members.add(self.foreign_owner)
        self.foreign_repo = Repository.objects.create(
            organization=self.foreign, name='outside-repo',
        )
        self.foreign_collab = RepositoryCollaborator.objects.create(
            user=self.foreign_owner, repository=self.foreign_repo,
        )
        self.foreign_collab.permissions.add(self.read)
        self.foreign_grant = TeamRepositoryPermission.objects.create(
            team=self.foreign_team,
            repository=self.foreign_repo,
            operation=self.read,
        )
        self.perm_only = create_user(
            'perm-only', password='perm-only-dev-only', is_staff=True,
        )
        self.perm_only.user_permissions.set(self.owner.user_permissions.all())
        self.inactive = create_user(
            'inactive-grant',
            password='inactive-grant-dev-only',
            is_staff=True,
        )
        self.inactive.is_active = False
        self.inactive.save(update_fields=['is_active'])
        self.inactive.user_permissions.set(self.owner.user_permissions.all())
        self.inactive_collab = RepositoryCollaborator.objects.create(
            user=self.inactive, repository=self.foreign_repo,
        )
        self.inactive_collab.permissions.add(self.read)

    def _admin_login(self, username, password):
        client = Client()
        response = client.post(reverse('admin:login'), {
            'username': username,
            'password': password,
            'next': reverse('admin:index'),
        })
        return client, response

    def _owner_client(self):
        client, response = self._admin_login(
            OWNER_USERNAME, DEVELOPMENT_PASSWORDS[OWNER_USERNAME],
        )
        self.assertRedirects(response, reverse('admin:index'))
        return client

    def test_seeded_owner_logs_in_and_sees_only_owned_rows(self):
        self.assertEqual(settings.AUTHENTICATION_BACKENDS, RUNNABLE_BACKENDS)
        self.assertTrue(self.owner.has_perm('gh_permissions.view_organization'))
        self.assertTrue(self.owner.has_perm('gh_permissions.change_repository'))
        self.assertTrue(self.owner.has_perm('gh_permissions.add_team'))
        self.assertFalse(self.owner.has_perm('gh_permissions.manage_organization'))
        self.assertFalse(
            self.owner.has_perm('gh_permissions.view_organization', self.foreign)
        )
        self.assertTrue(
            self.owner.has_perm(
                'gh_permissions.manage_organization', self.organization,
            )
        )
        self.assertFalse(
            self.owner.has_perm('gh_permissions.manage_organization', self.foreign)
        )
        self.assertTrue(
            self.direct.has_perm('gh_permissions.read_repository', self.repository)
        )
        self.assertFalse(self.direct.has_perm('gh_permissions.view_repository'))

        client = self._owner_client()
        home = client.get(reverse('admin:index'))
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, DENIAL)
        self.assertContains(
            home, reverse('admin:gh_permissions_organization_changelist'),
        )

        expected = {
            'admin:gh_permissions_organization_changelist': {
                self.organization.pk,
                self.owner.personal_organization.pk,
            },
            'admin:gh_permissions_team_changelist': {self.team.pk},
            'admin:gh_permissions_repository_changelist': {self.repository.pk},
            'admin:gh_permissions_repositorycollaborator_changelist': {
                self.collaboration.pk,
            },
            'admin:gh_permissions_teamrepositorypermission_changelist': {
                grant.pk for grant in self.grants
            },
            'admin:gh_permissions_organizationownership_changelist': set(
                OrganizationOwnership.objects.filter(user=self.owner).values_list(
                    'pk', flat=True,
                )
            ),
        }
        self.assertEqual(
            expected['admin:gh_permissions_organizationownership_changelist'],
            {
                OrganizationOwnership.objects.get(
                    user=self.owner, organization=self.organization,
                ).pk,
                OrganizationOwnership.objects.get(
                    user=self.owner,
                    organization=self.owner.personal_organization,
                ).pk,
            },
        )
        hidden_names = (
            'Outside Seed',
            'outside-team',
            'outside-repo',
            DIRECT_USERNAME,
            TEAM_USERNAME,
            OUTSIDER_USERNAME,
            SUPERUSER_USERNAME,
            'foreign-owner',
            'perm-only',
            'inactive-grant',
        )
        for url_name, pks in expected.items():
            response = client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)
            self.assertEqual(_listed_pks(response), pks, url_name)
            for name in hidden_names:
                self.assertNotContains(response, name)
        self.assertContains(
            client.get(reverse('admin:gh_permissions_organization_changelist')),
            DEFAULT_ORGANIZATION_NAME,
        )
        self.assertContains(
            client.get(reverse('admin:gh_permissions_organization_changelist')),
            OWNER_USERNAME,
        )
        self.assertContains(
            client.get(reverse('admin:gh_permissions_team_changelist')),
            TEAM_NAME,
        )
        self.assertContains(
            client.get(reverse('admin:gh_permissions_repository_changelist')),
            REPOSITORY_TITLE,
        )
        self.assertEqual(
            client.get(reverse('admin:example_user_changelist')).status_code,
            403,
        )
        self.assertEqual(
            client.get(reverse('admin:auth_permission_changelist')).status_code,
            403,
        )

    def test_foreign_urls_fail_closed(self):
        client = self._owner_client()
        owned = client.get(
            reverse('admin:gh_permissions_team_change', args=[self.team.pk]),
        )
        self.assertEqual(owned.status_code, 200)
        before = _graph_signature()
        targets = (
            ('admin:gh_permissions_organization_change', self.foreign.pk),
            (
                'admin:gh_permissions_organization_change',
                self.direct.personal_organization.pk,
            ),
            ('admin:gh_permissions_team_change', self.foreign_team.pk),
            ('admin:gh_permissions_team_history', self.foreign_team.pk),
            ('admin:gh_permissions_team_delete', self.foreign_team.pk),
            ('admin:gh_permissions_repository_change', self.foreign_repo.pk),
            (
                'admin:gh_permissions_repositorycollaborator_change',
                self.foreign_collab.pk,
            ),
            (
                'admin:gh_permissions_repositorycollaborator_change',
                self.inactive_collab.pk,
            ),
            (
                'admin:gh_permissions_teamrepositorypermission_change',
                self.foreign_grant.pk,
            ),
            (
                'admin:gh_permissions_organizationownership_change',
                self.foreign_ownership.pk,
            ),
            ('admin:gh_permissions_team_change', 999999),
        )
        for url_name, pk in targets:
            url = reverse(url_name, args=[pk])
            got = client.get(url)
            self.assertEqual(got.status_code, 302, url)
            self.assertEqual(got['Location'], reverse('admin:index'), url)
            with CaptureQueriesContext(connection) as captured:
                posted = client.post(url, {'post': 'yes', '_save': 'Save'})
            self.assertEqual(posted.status_code, 302, url)
            self.assertEqual(posted['Location'], reverse('admin:index'), url)
            self.assertEqual(_domain_writes(captured), [], url)
        self.assertEqual(_graph_signature(), before)

    def test_forged_foreign_parents_do_not_write(self):
        client = self._owner_client()
        before = _graph_signature()
        posts = (
            (
                reverse('admin:gh_permissions_team_add'),
                {
                    'name': 'smuggled',
                    'organization': str(self.foreign.pk),
                    '_save': 'Save',
                },
            ),
            (
                reverse('admin:gh_permissions_team_change', args=[self.team.pk]),
                {
                    'name': TEAM_NAME,
                    'organization': str(self.foreign.pk),
                    'members': [str(self.team_user.pk)],
                    'allowed_operations': [str(self.read.pk)],
                    '_save': 'Save',
                },
            ),
            (
                reverse('admin:gh_permissions_repository_add'),
                {
                    'name': 'smuggled-repo',
                    'organization': str(self.foreign.pk),
                    '_save': 'Save',
                },
            ),
            (
                reverse(
                    'admin:gh_permissions_repository_change',
                    args=[self.repository.pk],
                ),
                {
                    'name': REPOSITORY_TITLE,
                    'organization': str(self.foreign.pk),
                    '_save': 'Save',
                },
            ),
            (
                reverse('admin:gh_permissions_repositorycollaborator_add'),
                {
                    'user': str(self.outsider.pk),
                    'repository': str(self.foreign_repo.pk),
                    'permissions': [str(self.read.pk)],
                    '_save': 'Save',
                },
            ),
            (
                reverse(
                    'admin:gh_permissions_repositorycollaborator_change',
                    args=[self.collaboration.pk],
                ),
                {
                    'user': str(self.direct.pk),
                    'repository': str(self.foreign_repo.pk),
                    'permissions': [str(self.read.pk), str(self.write.pk)],
                    '_save': 'Save',
                },
            ),
            (
                reverse('admin:gh_permissions_teamrepositorypermission_add'),
                {
                    'team': str(self.foreign_team.pk),
                    'repository': str(self.foreign_repo.pk),
                    'operation': str(self.write.pk),
                    '_save': 'Save',
                },
            ),
        )
        for url, data in posts:
            with CaptureQueriesContext(connection) as captured:
                response = client.post(url, data)
            self.assertEqual(response.status_code, 200, url)
            self.assertContains(response, 'valid choice')
            self.assertEqual(_domain_writes(captured), [], url)
        with CaptureQueriesContext(connection) as captured:
            denied = client.post(
                reverse('admin:gh_permissions_organizationownership_add'),
                {
                    'user': str(self.owner.pk),
                    'organization': str(self.foreign.pk),
                    '_save': 'Save',
                },
            )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(_domain_writes(captured), [])
        self.assertEqual(_graph_signature(), before)
        self.assertFalse(Team.objects.filter(name='smuggled').exists())
        self.assertFalse(Repository.objects.filter(name='smuggled-repo').exists())
        self.team.refresh_from_db()
        self.repository.refresh_from_db()
        self.collaboration.refresh_from_db()
        self.assertEqual(self.team.organization_id, self.organization.pk)
        self.assertEqual(self.repository.organization_id, self.organization.pk)
        self.assertEqual(self.collaboration.repository_id, self.repository.pk)

    def test_object_grants_do_not_open_admin(self):
        self.assertTrue(
            self.direct.has_perm('gh_permissions.write_repository', self.repository)
        )
        self.assertTrue(
            self.team_user.has_perm('gh_permissions.read_repository', self.repository)
        )
        self.assertFalse(
            self.team_user.has_perm(
                'gh_permissions.write_repository', self.repository,
            )
        )
        self.assertTrue(
            self.outsider.has_perm(
                'gh_permissions.manage_organization',
                self.outsider.personal_organization,
            )
        )
        self.assertFalse(
            self.outsider.has_perm(
                'gh_permissions.manage_organization', self.organization,
            )
        )
        self.assertFalse(self.inactive.has_perm('gh_permissions.view_organization'))
        self.assertTrue(self.inactive.is_staff)
        self.assertTrue(self.inactive.user_permissions.exists())
        self.assertTrue(
            OrganizationOwnership.objects.filter(user=self.inactive).exists()
        )
        self.assertTrue(
            RepositoryCollaborator.objects.filter(user=self.inactive).exists()
        )

        for username in (DIRECT_USERNAME, TEAM_USERNAME, OUTSIDER_USERNAME):
            client, response = self._admin_login(
                username, DEVELOPMENT_PASSWORDS[username],
            )
            self.assertEqual(response.status_code, 200, username)
            self.assertContains(response, 'staff account')
            self.assertNotIn('_auth_user_id', client.session)
            session = Client()
            self.assertTrue(session.login(
                username=username,
                password=DEVELOPMENT_PASSWORDS[username],
            ))
            denied = session.get(
                reverse('admin:gh_permissions_organization_changelist'),
            )
            self.assertEqual(denied.status_code, 302, username)
            self.assertIn('/admin/login/', denied['Location'])

        inactive_client, inactive_response = self._admin_login(
            'inactive-grant', 'inactive-grant-dev-only',
        )
        self.assertEqual(inactive_response.status_code, 200)
        self.assertNotIn('_auth_user_id', inactive_client.session)
        self.assertFalse(Client().login(
            username='inactive-grant',
            password='inactive-grant-dev-only',
        ))

    def test_model_permissions_are_not_organization_authority(self):
        self.assertTrue(self.perm_only.has_perm('gh_permissions.view_organization'))
        self.assertTrue(self.perm_only.has_perm('gh_permissions.add_team'))
        self.assertFalse(
            self.perm_only.has_perm(
                'gh_permissions.manage_organization', self.organization,
            )
        )
        self.assertFalse(
            self.perm_only.has_perm(
                'gh_permissions.manage_organization', self.foreign,
            )
        )
        client, response = self._admin_login(
            'perm-only', 'perm-only-dev-only',
        )
        self.assertRedirects(response, reverse('admin:index'))
        listed = client.get(reverse('admin:gh_permissions_organization_changelist'))
        self.assertEqual(
            _listed_pks(listed),
            {self.perm_only.personal_organization.pk},
        )
        self.assertNotContains(listed, DEFAULT_ORGANIZATION_NAME)
        self.assertNotContains(listed, 'Outside Seed')
        ownership = OrganizationOwnership.objects.get(
            user=self.perm_only,
            organization=self.perm_only.personal_organization,
        )
        expected_empty = CHANGELISTS[1:-1]
        for url_name in expected_empty:
            response = client.get(reverse(url_name))
            self.assertEqual(_listed_pks(response), set(), url_name)
        owned_rows = client.get(reverse(CHANGELISTS[-1]))
        self.assertEqual(_listed_pks(owned_rows), {ownership.pk})
        self.assertEqual(
            client.get(
                reverse(
                    'admin:gh_permissions_organization_change',
                    args=[self.organization.pk],
                ),
            ).status_code,
            302,
        )

    def test_superuser_bypass_stays_explicit(self):
        client, response = self._admin_login(
            SUPERUSER_USERNAME, DEVELOPMENT_PASSWORDS[SUPERUSER_USERNAME],
        )
        self.assertRedirects(response, reverse('admin:index'))
        listed = client.get(reverse('admin:gh_permissions_organization_changelist'))
        self.assertEqual(
            _listed_pks(listed),
            set(Organization.objects.values_list('pk', flat=True)),
        )
        self.assertContains(listed, DEFAULT_ORGANIZATION_NAME)
        self.assertContains(listed, 'Outside Seed')
        self.assertContains(listed, OWNER_USERNAME)
        self.assertContains(listed, DIRECT_USERNAME)
        foreign_team = client.get(
            reverse('admin:gh_permissions_team_change', args=[self.foreign_team.pk]),
        )
        self.assertEqual(foreign_team.status_code, 200)
        self.assertEqual(
            set(Organization.objects.authorized(self.superuser, self.manage)),
            {self.superuser.personal_organization},
        )
        self.assertNotIn(
            self.organization,
            set(Organization.objects.authorized(self.superuser, self.manage)),
        )
        self.assertNotIn(
            self.foreign,
            set(Organization.objects.authorized(self.superuser, self.manage)),
        )
        self.assertTrue(
            self.superuser.has_perm(
                'gh_permissions.manage_organization', self.organization,
            )
        )
        self.assertIn(
            self.organization,
            set(Organization.objects.authorized(self.owner, self.manage)),
        )
