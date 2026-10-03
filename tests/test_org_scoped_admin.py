"""Hostile admin requests for organization-owner grant management.

A filtered changelist is not enough. These tests drive Django's stock
admin routes as superusers, organization owners, empty-handed staff,
and ordinary users.
"""

from html.parser import HTMLParser
from pathlib import Path

from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse

from gh_permissions.admin import (
    OrganizationAdmin,
    OrgScopedAdmin,
    RepositoryAdmin,
    TeamAdmin,
    TeamRepositoryPermissionAdmin,
    UserRepositoryPermissionAdmin,
)
from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import (
    Operation,
    Organization,
    Repository,
    Team,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)
from trusts.apps import implementation_for_path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
    'django.contrib.auth.backends.ModelBackend',
)
SCOPED_MODELS = (
    Organization,
    Team,
    Repository,
    UserRepositoryPermission,
    TeamRepositoryPermission,
)
CONCRETE_ADMINS = (
    OrganizationAdmin,
    TeamAdmin,
    RepositoryAdmin,
    UserRepositoryPermissionAdmin,
    TeamRepositoryPermissionAdmin,
)


def _registry():
    return implementation_for_path(CANONICAL_BACKEND).configured_backend(
        CANONICAL_BACKEND,
    ).registry


class _SelectParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._select = None
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'select':
            self._select = attrs.get('name')
            self.values.setdefault(self._select, [])
        elif tag == 'option' and self._select:
            self.values[self._select].append(attrs.get('value'))

    def handle_endtag(self, tag):
        if tag == 'select':
            self._select = None


def _select_values(html, name):
    parser = _SelectParser()
    parser.feed(html)
    return [value for value in parser.values.get(name, []) if value]


def _grant_scoped(user):
    codenames = []
    for model in SCOPED_MODELS:
        for action in ('view', 'add', 'change', 'delete'):
            codenames.append('%s_%s' % (action, model._meta.model_name))
    permissions = Permission.objects.filter(
        content_type__app_label='gh_permissions',
        codename__in=codenames,
    )
    user.user_permissions.set(permissions)


def _client_for(user):
    client = Client()
    client.force_login(user, backend=ADMIN_BACKENDS[1])
    return client


class OrgScopedAdminContractTests(SimpleTestCase):
    def test_security_logic_lives_on_the_mixin_only(self):
        hooks = (
            'get_queryset',
            'has_view_permission',
            'has_change_permission',
            'has_delete_permission',
            'has_add_permission',
            'formfield_for_foreignkey',
            'formfield_for_manytomany',
            'save_model',
            'get_form',
        )
        for admin_cls in CONCRETE_ADMINS:
            self.assertTrue(issubclass(admin_cls, OrgScopedAdmin))
            self.assertTrue(issubclass(admin_cls, admin.ModelAdmin))
            for hook in hooks:
                self.assertNotIn(hook, admin_cls.__dict__)
            self.assertIn('organization_path', admin_cls.__dict__)
            self.assertEqual(admin_cls.list_filter, ())
            self.assertEqual(admin_cls.search_fields, ())
            self.assertEqual(admin_cls.autocomplete_fields, ())
            self.assertEqual(admin_cls.raw_id_fields, ())
            self.assertEqual(admin_cls.inlines, ())
            self.assertEqual(admin_cls.list_editable, ())

    def test_operation_stays_a_bare_model_admin(self):
        registered = admin.site._registry[Operation]
        self.assertIs(type(registered), admin.ModelAdmin)
        self.assertNotIsInstance(registered, OrgScopedAdmin)

    def test_no_custom_admin_templates_or_views(self):
        self.assertFalse((ROOT / 'gh_permissions' / 'templates').exists())
        self.assertFalse((ROOT / 'gh_permissions' / 'views.py').exists())

    def test_owner_migration_is_a_nullable_add_field(self):
        text = (
            ROOT / 'gh_permissions' / 'migrations' / '0002_organization_owner.py'
        ).read_text()
        self.assertIn('AddField', text)
        self.assertIn('null=True', text)
        self.assertNotIn('RemoveField', text)
        self.assertNotIn('DeleteModel', text)
        self.assertNotIn('RunPython', text)

    def test_user_facing_copy_keeps_role_like_team_disclaimer(self):
        readme = (ROOT / 'README.md').read_text()
        design = (ROOT / 'docs' / 'org-scoped-admin.md').read_text()
        disclaimer = (
            'This bounded example does not reproduce GitHub directly. '
            'Its `Team` is role-like: it groups members, carries an '
            'allowed-operation ceiling, and receives repository grants. '
            'It does not model the broader collaboration behavior of a '
            'real GitHub team.'
        )
        self.assertIn(' '.join(disclaimer.split()), ' '.join(readme.split()))
        self.assertIn(' '.join(disclaimer.split()), ' '.join(design.split()))
        self.assertNotIn('auth.Group', (ROOT / 'gh_permissions' / 'models.py').read_text())


class OrganizationOwnerMigrationTests(TransactionTestCase):
    def tearDown(self):
        call_command(
            'migrate', 'gh_permissions', verbosity=0, interactive=False,
        )
        super().tearDown()

    def test_upgrade_from_initial_preserves_rows_and_leaves_owner_null(self):
        call_command(
            'migrate', 'gh_permissions', '0001_initial',
            verbosity=0, interactive=False,
        )
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, 'gh_permissions_organization',
                )
            }
        self.assertNotIn('owner_id', columns)
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO gh_permissions_organization (name) "
                "VALUES ('legacy-org')"
            )
        call_command(
            'migrate', 'gh_permissions', verbosity=0, interactive=False,
        )
        org = Organization.objects.get(name='legacy-org')
        self.assertIsNone(org.owner_id)
        self.assertEqual(Organization.objects.count(), 1)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT owner_id FROM gh_permissions_organization "
                "WHERE name = 'legacy-org'"
            )
            self.assertIsNone(cursor.fetchone()[0])
        self.assertTrue(Organization._meta.get_field('owner').null)


@override_settings(AUTHENTICATION_BACKENDS=ADMIN_BACKENDS)
class OrgScopedAdminRequestTests(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.owner_a = User.objects.create(
            username='owner-a', is_staff=True, is_active=True,
        )
        self.owner_b = User.objects.create(
            username='owner-b', is_staff=True, is_active=True,
        )
        self.staff_empty = User.objects.create(
            username='staff-empty', is_staff=True, is_active=True,
        )
        self.superuser = User.objects.create(
            username='root', is_staff=True, is_superuser=True, is_active=True,
        )
        self.inactive = User.objects.create(
            username='inactive-staff', is_staff=True, is_active=False,
        )
        self.target = User.objects.create(username='grant-target', is_active=True)
        self.member = User.objects.create(username='already-member', is_active=True)
        for user in (self.owner_a, self.owner_b, self.staff_empty):
            _grant_scoped(user)
        self.org_a = Organization.objects.create(name='owned-alpha', owner=self.owner_a)
        self.org_a2 = Organization.objects.create(name='owned-delta', owner=self.owner_a)
        self.org_b = Organization.objects.create(name='foreign-beta', owner=self.owner_b)
        self.org_free = Organization.objects.create(name='unowned-gamma')
        self.team_a = Team.objects.create(organization=self.org_a, name='alpha-writers')
        self.team_b = Team.objects.create(organization=self.org_b, name='beta-outsiders')
        self.team_a.members.add(self.member)
        self.read = Operation.objects.create(code='read')
        self.write = Operation.objects.create(code='write')
        self.team_a.allowed_operations.add(self.read)
        self.repo_a = Repository.objects.create(
            organization=self.org_a, title='alpha-repo',
        )
        self.repo_b = Repository.objects.create(
            organization=self.org_b, title='beta-repo',
        )
        self.repo_a2 = Repository.objects.create(
            organization=self.org_a2, title='delta-repo',
        )
        self.owner_client = _client_for(self.owner_a)
        self.foreign_client = _client_for(self.owner_b)
        self.empty_client = _client_for(self.staff_empty)
        self.super_client = _client_for(self.superuser)
        self.member_client = _client_for(self.member)

    def _closed(self, client, url, data=None):
        if data is None:
            response = client.get(url)
        else:
            response = client.post(url, data)
        self.assertEqual(response.status_code, 302, response.content[:500])
        self.assertEqual(response['Location'], reverse('admin:index'))
        return response

    def test_superuser_sees_every_organization_and_stock_catalogs(self):
        response = self.super_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'owned-alpha')
        self.assertContains(response, 'foreign-beta')
        self.assertContains(response, 'unowned-gamma')
        self.assertContains(response, 'owned-delta')
        names = [template.name for template in response.templates if template.name]
        self.assertIn('admin/change_list.html', names)
        for url_name in (
            'admin:gh_permissions_team_change',
            'admin:gh_permissions_repository_change',
        ):
            owned = self.super_client.get(reverse(url_name, args=[self.team_a.pk if 'team' in url_name else self.repo_a.pk]))
            foreign = self.super_client.get(reverse(url_name, args=[self.team_b.pk if 'team' in url_name else self.repo_b.pk]))
            self.assertEqual(owned.status_code, 200)
            self.assertEqual(foreign.status_code, 200)
        self.assertEqual(
            self.super_client.get(reverse('admin:gh_permissions_operation_changelist')).status_code,
            200,
        )
        self.assertEqual(
            self.super_client.get(
                reverse('admin:auth_user_change', args=[self.target.pk]),
            ).status_code,
            200,
        )
        created = self.super_client.post(
            reverse('admin:gh_permissions_organization_add'),
            {'name': 'super-added', 'owner': str(self.owner_a.pk), '_save': 'Save'},
        )
        self.assertEqual(created.status_code, 302)
        self.assertTrue(
            Organization.objects.filter(name='super-added', owner=self.owner_a).exists(),
        )

    def test_owner_changelist_is_sql_filtered_before_pagination(self):
        Team.objects.create(organization=self.org_a, name='alpha-readers')
        admin_obj = admin.site._registry[Team]
        previous = admin_obj.list_per_page
        admin_obj.list_per_page = 1
        try:
            url = reverse('admin:gh_permissions_team_changelist')
            with CaptureQueriesContext(connection) as captured:
                page = self.owner_client.get(url)
            self.assertEqual(page.status_code, 200)
            limited = [
                query['sql'] for query in captured.captured_queries
                if 'LIMIT' in query['sql'] and 'gh_permissions_team' in query['sql']
            ]
            self.assertTrue(limited)
            for sql in limited:
                self.assertIn('owner_id', sql)
            self.assertContains(page, '2 teams')
            self.assertNotContains(page, 'beta-outsiders')
            self.assertNotContains(page, 'foreign-beta')
            second = self.owner_client.get(url + '?p=2')
            self.assertEqual(second.status_code, 200)
            shown = page.content.decode() + second.content.decode()
            self.assertIn('alpha-writers', shown)
            self.assertIn('alpha-readers', shown)
            self.assertNotIn('beta-outsiders', shown)
        finally:
            admin_obj.list_per_page = previous

    def test_owner_sees_only_owned_rows_on_each_changelist(self):
        UserRepositoryPermission.objects.create(
            user=self.target, repository=self.repo_a, operation=self.read,
        )
        UserRepositoryPermission.objects.create(
            user=self.target, repository=self.repo_b, operation=self.read,
        )
        TeamRepositoryPermission.objects.create(
            team=self.team_a, repository=self.repo_a, operation=self.read,
        )
        TeamRepositoryPermission.objects.create(
            team=self.team_b, repository=self.repo_b, operation=self.read,
        )
        expectations = (
            ('admin:gh_permissions_organization_changelist', 'owned-alpha', 'foreign-beta'),
            ('admin:gh_permissions_team_changelist', 'alpha-writers', 'beta-outsiders'),
            ('admin:gh_permissions_repository_changelist', 'alpha-repo', 'beta-repo'),
        )
        for url_name, owned, foreign in expectations:
            response = self.owner_client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, owned)
            self.assertNotContains(response, foreign)
            self.assertNotContains(response, 'unowned-gamma')
        direct = self.owner_client.get(
            reverse('admin:gh_permissions_userrepositorypermission_changelist'),
        )
        self.assertEqual(direct.status_code, 200)
        self.assertEqual(UserRepositoryPermission.objects.count(), 2)
        self.assertContains(direct, '1 user repository permission')
        team_grants = self.owner_client.get(
            reverse('admin:gh_permissions_teamrepositorypermission_changelist'),
        )
        self.assertContains(team_grants, '1 team repository permission')

    def test_guessed_cross_org_change_history_and_delete_urls_fail_closed(self):
        before = list(Team.objects.order_by('pk').values_list('pk', 'name', 'organization_id'))
        urls = (
            reverse('admin:gh_permissions_team_change', args=[self.team_b.pk]),
            reverse('admin:gh_permissions_team_history', args=[self.team_b.pk]),
            reverse('admin:gh_permissions_team_delete', args=[self.team_b.pk]),
            reverse('admin:gh_permissions_repository_change', args=[self.repo_b.pk]),
            reverse('admin:gh_permissions_organization_change', args=[self.org_b.pk]),
            reverse('admin:gh_permissions_organization_change', args=[self.org_free.pk]),
            reverse('admin:gh_permissions_team_change', args=[999999]),
        )
        for url in urls:
            self._closed(self.owner_client, url)
            self._closed(self.owner_client, url, {'post': 'yes', '_save': 'Save'})
        self.assertEqual(
            list(Team.objects.order_by('pk').values_list('pk', 'name', 'organization_id')),
            before,
        )
        self.assertTrue(Organization.objects.filter(pk=self.org_b.pk, owner=self.owner_b).exists())
        owned_history = self.owner_client.get(
            reverse('admin:gh_permissions_team_history', args=[self.team_a.pk]),
        )
        self.assertEqual(owned_history.status_code, 200)
        names = [template.name for template in owned_history.templates if template.name]
        self.assertIn('admin/object_history.html', names)

    def test_forged_add_and_change_posts_cannot_name_another_organization(self):
        add = self.owner_client.post(
            reverse('admin:gh_permissions_team_add'),
            {
                'name': 'smuggled',
                'organization': str(self.org_b.pk),
                'members': [str(self.target.pk)],
                'allowed_operations': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(add.status_code, 200)
        self.assertContains(add, 'valid choice')
        self.assertFalse(Team.objects.filter(name='smuggled').exists())

        forged_change = self.owner_client.post(
            reverse('admin:gh_permissions_team_change', args=[self.team_a.pk]),
            {
                'name': 'alpha-writers',
                'organization': str(self.org_b.pk),
                'members': [str(self.target.pk)],
                'allowed_operations': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(forged_change.status_code, 200)
        self.team_a.refresh_from_db()
        self.assertEqual(self.team_a.organization_id, self.org_a.pk)
        self.assertNotIn(self.target, self.team_a.members.all())

        save_as_new = self.owner_client.post(
            reverse('admin:gh_permissions_team_change', args=[self.team_b.pk]),
            {
                'name': 'copied-foreign',
                'organization': str(self.org_b.pk),
                '_saveasnew': 'Save as new',
            },
        )
        self.assertEqual(save_as_new.status_code, 200)
        self.assertFalse(Team.objects.filter(name='copied-foreign').exists())
        self.assertTrue(Team.objects.filter(pk=self.team_b.pk, organization=self.org_b).exists())

        copied_home = self.owner_client.post(
            reverse('admin:gh_permissions_team_change', args=[self.team_b.pk]),
            {
                'name': 'copied-home',
                'organization': str(self.org_a.pk),
                '_saveasnew': 'Save as new',
            },
        )
        self.assertEqual(copied_home.status_code, 302)
        self.assertTrue(
            Team.objects.filter(name='copied-home', organization=self.org_a).exists(),
        )
        self.assertTrue(Team.objects.filter(pk=self.team_b.pk, organization=self.org_b).exists())

        to_field = self.owner_client.get(
            reverse('admin:gh_permissions_team_change', args=[self.team_b.pk])
            + '?_to_field=name',
        )
        self.assertEqual(to_field.status_code, 400)
        self.assertTrue(Team.objects.filter(pk=self.team_b.pk).exists())

    def test_foreign_key_choices_are_limited_to_owned_rows(self):
        add = self.owner_client.get(reverse('admin:gh_permissions_team_add'))
        self.assertEqual(
            set(_select_values(add.content.decode(), 'organization')),
            {str(self.org_a.pk), str(self.org_a2.pk)},
        )
        change = self.owner_client.get(
            reverse('admin:gh_permissions_repository_change', args=[self.repo_a.pk]),
        )
        self.assertEqual(
            set(_select_values(change.content.decode(), 'organization')),
            {str(self.org_a.pk), str(self.org_a2.pk)},
        )
        grant = self.owner_client.get(
            reverse('admin:gh_permissions_userrepositorypermission_add'),
        )
        html = grant.content.decode()
        self.assertEqual(
            set(_select_values(html, 'repository')),
            {str(self.repo_a.pk), str(self.repo_a2.pk)},
        )
        self.assertIn(str(self.target.pk), _select_values(html, 'user'))
        self.assertIn(str(self.owner_b.pk), _select_values(html, 'user'))
        self.assertIn(str(self.read.pk), _select_values(html, 'operation'))
        self.assertIn(str(self.write.pk), _select_values(html, 'operation'))
        self.assertNotIn('/admin/auth/user/', html)
        popup = self.owner_client.get(
            reverse('admin:gh_permissions_repository_changelist') + '?_popup=1',
        )
        self.assertContains(popup, 'alpha-repo')
        self.assertNotContains(popup, 'beta-repo')
        filtered = self.owner_client.get(
            reverse('admin:gh_permissions_team_changelist')
            + '?organization=%s' % self.org_b.pk,
        )
        self.assertNotContains(filtered, 'beta-outsiders')

    def test_team_member_choices_and_forged_member_posts(self):
        change = self.owner_client.get(
            reverse('admin:gh_permissions_team_change', args=[self.team_a.pk]),
        )
        members = set(_select_values(change.content.decode(), 'members'))
        for user in (self.target, self.member, self.owner_a, self.owner_b, self.superuser):
            self.assertIn(str(user.pk), members)
        forged = self.owner_client.post(
            reverse('admin:gh_permissions_team_change', args=[self.team_a.pk]),
            {
                'name': 'alpha-writers',
                'organization': str(self.org_a.pk),
                'members': ['999999'],
                'allowed_operations': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(forged.status_code, 200)
        self.assertEqual(set(self.team_a.members.values_list('pk', flat=True)), {self.member.pk})
        before_perms = set(self.owner_b.user_permissions.values_list('pk', flat=True))
        added = self.owner_client.post(
            reverse('admin:gh_permissions_team_change', args=[self.team_a.pk]),
            {
                'name': 'alpha-writers',
                'organization': str(self.org_a.pk),
                'members': [str(self.member.pk), str(self.owner_b.pk)],
                'allowed_operations': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(added.status_code, 302)
        self.assertIn(self.owner_b, self.team_a.members.all())
        self.owner_b.refresh_from_db()
        self.assertTrue(self.owner_b.is_staff)
        self.assertFalse(self.owner_b.is_superuser)
        self.assertEqual(
            set(self.owner_b.user_permissions.values_list('pk', flat=True)),
            before_perms,
        )
        self.assertEqual(
            self.owner_client.get(
                reverse('admin:auth_user_change', args=[self.owner_b.pk]),
            ).status_code,
            403,
        )
        foreign_index = self.foreign_client.get(
            reverse('admin:gh_permissions_team_changelist'),
        )
        self.assertContains(foreign_index, 'beta-outsiders')
        self.assertNotContains(foreign_index, 'alpha-writers')

    def test_direct_and_team_grants_create_update_and_revoke(self):
        registry = _registry()
        self.assertFalse(registry.has_permission(self.member, self.repo_a, self.read))
        self.assertFalse(registry.has_permission(self.owner_a, self.repo_a, self.read))

        forged_direct = self.owner_client.post(
            reverse('admin:gh_permissions_userrepositorypermission_add'),
            {
                'user': str(self.target.pk),
                'repository': str(self.repo_b.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(forged_direct.status_code, 200)
        self.assertEqual(UserRepositoryPermission.objects.count(), 0)

        created = self.owner_client.post(
            reverse('admin:gh_permissions_userrepositorypermission_add'),
            {
                'user': str(self.target.pk),
                'repository': str(self.repo_a.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(created.status_code, 302)
        direct = UserRepositoryPermission.objects.get()
        with self.assertNumQueries(1):
            self.assertTrue(
                registry.has_permission(self.target, self.repo_a, self.read),
            )
        updated = self.owner_client.post(
            reverse(
                'admin:gh_permissions_userrepositorypermission_change',
                args=[direct.pk],
            ),
            {
                'user': str(self.target.pk),
                'repository': str(self.repo_a.pk),
                'operation': str(self.write.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(updated.status_code, 302)
        direct.refresh_from_db()
        self.assertEqual(direct.operation_id, self.write.pk)
        self.assertFalse(registry.has_permission(self.target, self.repo_a, self.read))
        self.assertTrue(registry.has_permission(self.target, self.repo_a, self.write))
        retarget = self.owner_client.post(
            reverse(
                'admin:gh_permissions_userrepositorypermission_change',
                args=[direct.pk],
            ),
            {
                'user': str(self.target.pk),
                'repository': str(self.repo_b.pk),
                'operation': str(self.write.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(retarget.status_code, 200)
        direct.refresh_from_db()
        self.assertEqual(direct.repository_id, self.repo_a.pk)
        removed = self.owner_client.post(
            reverse(
                'admin:gh_permissions_userrepositorypermission_delete',
                args=[direct.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(removed.status_code, 302)
        self.assertEqual(UserRepositoryPermission.objects.count(), 0)
        self.assertFalse(registry.has_permission(self.target, self.repo_a, self.write))

        foreign_team_grant = self.owner_client.post(
            reverse('admin:gh_permissions_teamrepositorypermission_add'),
            {
                'team': str(self.team_b.pk),
                'repository': str(self.repo_a.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(foreign_team_grant.status_code, 200)
        misaligned = self.owner_client.post(
            reverse('admin:gh_permissions_teamrepositorypermission_add'),
            {
                'team': str(self.team_a.pk),
                'repository': str(self.repo_a2.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(misaligned.status_code, 200)
        self.assertContains(misaligned, 'must stay inside one organization')
        self.assertEqual(TeamRepositoryPermission.objects.count(), 0)
        self.super_client.post(
            reverse('admin:gh_permissions_teamrepositorypermission_add'),
            {
                'team': str(self.team_a.pk),
                'repository': str(self.repo_b.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(TeamRepositoryPermission.objects.count(), 0)

        over_ceiling = self.owner_client.post(
            reverse('admin:gh_permissions_teamrepositorypermission_add'),
            {
                'team': str(self.team_a.pk),
                'repository': str(self.repo_a.pk),
                'operation': str(self.write.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(over_ceiling.status_code, 302)
        self.assertFalse(registry.has_permission(self.member, self.repo_a, self.write))
        TeamRepositoryPermission.objects.all().delete()

        team_grant = self.owner_client.post(
            reverse('admin:gh_permissions_teamrepositorypermission_add'),
            {
                'team': str(self.team_a.pk),
                'repository': str(self.repo_a.pk),
                'operation': str(self.read.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(team_grant.status_code, 302)
        grant = TeamRepositoryPermission.objects.get()
        with self.assertNumQueries(1):
            self.assertTrue(registry.has_permission(self.member, self.repo_a, self.read))
        self.assertFalse(registry.has_permission(self.target, self.repo_a, self.read))
        self.owner_client.post(
            reverse('admin:gh_permissions_userrepositorypermission_add'),
            {
                'user': str(self.member.pk),
                'repository': str(self.repo_a.pk),
                'operation': str(self.write.pk),
                '_save': 'Save',
            },
        )
        self.assertTrue(registry.has_permission(self.member, self.repo_a, self.read))
        self.assertTrue(registry.has_permission(self.member, self.repo_a, self.write))
        direct = UserRepositoryPermission.objects.get()
        self.owner_client.post(
            reverse(
                'admin:gh_permissions_userrepositorypermission_delete',
                args=[direct.pk],
            ),
            {'post': 'yes'},
        )
        self.assertTrue(registry.has_permission(self.member, self.repo_a, self.read))
        self.assertFalse(registry.has_permission(self.member, self.repo_a, self.write))
        self.owner_client.post(
            reverse(
                'admin:gh_permissions_teamrepositorypermission_delete',
                args=[grant.pk],
            ),
            {'post': 'yes'},
        )
        self.assertFalse(registry.has_permission(self.member, self.repo_a, self.read))
        self.assertIn(self.member, self.team_a.members.all())
        self.assertFalse(registry.has_permission(self.owner_a, self.repo_a, self.read))

    def test_bulk_delete_selected_cannot_reach_another_organization(self):
        other_owned = Team.objects.create(organization=self.org_a, name='alpha-keepers')
        TeamRepositoryPermission.objects.create(
            team=self.team_a, repository=self.repo_a, operation=self.read,
        )
        response = self.owner_client.post(
            reverse('admin:gh_permissions_team_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '0',
                ACTION_CHECKBOX_NAME: [str(self.team_a.pk), str(self.team_b.pk)],
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Team.objects.filter(pk=self.team_a.pk).exists())
        self.assertFalse(TeamRepositoryPermission.objects.filter(team_id=self.team_a.pk).exists())
        self.assertTrue(Team.objects.filter(pk=self.team_b.pk).exists())
        self.assertTrue(Repository.objects.filter(pk=self.repo_a.pk).exists())
        self.assertTrue(Team.objects.filter(pk=other_owned.pk).exists())

        across = self.owner_client.post(
            reverse('admin:gh_permissions_team_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '1',
                ACTION_CHECKBOX_NAME: [str(self.team_b.pk)],
            },
        )
        self.assertEqual(across.status_code, 302)
        self.assertFalse(Team.objects.filter(pk=other_owned.pk).exists())
        self.assertTrue(Team.objects.filter(pk=self.team_b.pk, organization=self.org_b).exists())
        self.assertTrue(Repository.objects.filter(pk=self.repo_b.pk).exists())

    def test_direct_delete_cascades_inside_the_organization_only(self):
        UserRepositoryPermission.objects.create(
            user=self.target, repository=self.repo_a, operation=self.read,
        )
        TeamRepositoryPermission.objects.create(
            team=self.team_a, repository=self.repo_a, operation=self.read,
        )
        UserRepositoryPermission.objects.create(
            user=self.target, repository=self.repo_b, operation=self.write,
        )
        response = self.owner_client.post(
            reverse('admin:gh_permissions_repository_delete', args=[self.repo_a.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Repository.objects.filter(pk=self.repo_a.pk).exists())
        self.assertFalse(UserRepositoryPermission.objects.filter(repository=self.repo_a).exists())
        self.assertFalse(TeamRepositoryPermission.objects.filter(repository=self.repo_a).exists())
        self.assertTrue(Team.objects.filter(pk=self.team_a.pk).exists())
        self.assertTrue(Repository.objects.filter(pk=self.repo_b.pk).exists())
        self.assertTrue(
            UserRepositoryPermission.objects.filter(repository=self.repo_b).exists(),
        )
        self._closed(
            self.owner_client,
            reverse('admin:gh_permissions_repository_delete', args=[self.repo_b.pk]),
            {'post': 'yes'},
        )
        self.assertTrue(Repository.objects.filter(pk=self.repo_b.pk).exists())

    def test_misaligned_grant_blocks_owner_cascade_until_superuser_removes_it(self):
        grant = TeamRepositoryPermission.objects.create(
            team=self.team_a, repository=self.repo_b, operation=self.read,
        )
        confirmation = self.owner_client.get(
            reverse('admin:gh_permissions_team_delete', args=[self.team_a.pk]),
        )
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, 'team repository permission')
        self.assertNotContains(confirmation, 'beta-repo')
        denied = self.owner_client.post(
            reverse('admin:gh_permissions_team_delete', args=[self.team_a.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertTrue(Team.objects.filter(pk=self.team_a.pk).exists())
        self.assertTrue(Repository.objects.filter(pk=self.repo_b.pk).exists())
        self.assertTrue(TeamRepositoryPermission.objects.filter(pk=grant.pk).exists())
        self._closed(
            self.owner_client,
            reverse(
                'admin:gh_permissions_teamrepositorypermission_delete',
                args=[grant.pk],
            ),
            {'post': 'yes'},
        )
        removed = self.super_client.post(
            reverse(
                'admin:gh_permissions_teamrepositorypermission_delete',
                args=[grant.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(removed.status_code, 302)
        self.assertFalse(TeamRepositoryPermission.objects.filter(pk=grant.pk).exists())
        deleted = self.owner_client.post(
            reverse('admin:gh_permissions_team_delete', args=[self.team_a.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(Team.objects.filter(pk=self.team_a.pk).exists())
        self.assertTrue(Repository.objects.filter(pk=self.repo_b.pk).exists())

    def test_ownership_cannot_be_moved_by_an_owner(self):
        renamed = self.owner_client.post(
            reverse('admin:gh_permissions_organization_change', args=[self.org_a.pk]),
            {
                'name': 'owned-alpha-renamed',
                'owner': str(self.owner_b.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(renamed.status_code, 302)
        self.org_a.refresh_from_db()
        self.assertEqual(self.org_a.name, 'owned-alpha-renamed')
        self.assertEqual(self.org_a.owner_id, self.owner_a.pk)
        add = self.owner_client.post(
            reverse('admin:gh_permissions_organization_add'),
            {'name': 'stolen', 'owner': str(self.owner_a.pk), '_save': 'Save'},
        )
        self.assertEqual(add.status_code, 403)
        self.assertFalse(Organization.objects.filter(name='stolen').exists())
        moved = self.owner_client.post(
            reverse('admin:gh_permissions_team_change', args=[self.team_a.pk]),
            {
                'name': 'alpha-writers',
                'organization': str(self.org_a2.pk),
                'members': [str(self.member.pk)],
                'allowed_operations': [str(self.read.pk)],
                '_save': 'Save',
            },
        )
        self.assertEqual(moved.status_code, 302)
        self.team_a.refresh_from_db()
        self.assertEqual(self.team_a.organization_id, self.org_a2.pk)
        self.assertNotContains(
            self.foreign_client.get(reverse('admin:gh_permissions_team_changelist')),
            'alpha-writers',
        )
        reassigned = self.super_client.post(
            reverse('admin:gh_permissions_organization_change', args=[self.org_b.pk]),
            {
                'name': 'foreign-beta',
                'owner': str(self.owner_a.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(reassigned.status_code, 302)
        self.org_b.refresh_from_db()
        self.assertEqual(self.org_b.owner_id, self.owner_a.pk)
        self.assertContains(
            self.owner_client.get(
                reverse('admin:gh_permissions_organization_changelist'),
            ),
            'foreign-beta',
        )
        self.assertNotContains(
            self.foreign_client.get(
                reverse('admin:gh_permissions_organization_changelist'),
            ),
            'foreign-beta',
        )

    def test_staff_with_permissions_and_no_owned_organization_mutates_nothing(self):
        listing = self.empty_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, 'owned-alpha')
        self.assertNotContains(listing, 'foreign-beta')
        self.assertNotContains(listing, 'unowned-gamma')
        self.assertEqual(
            self.empty_client.get(reverse('admin:gh_permissions_team_add')).status_code,
            403,
        )
        self.assertEqual(
            self.empty_client.post(
                reverse('admin:gh_permissions_team_add'),
                {
                    'name': 'empty-team',
                    'organization': str(self.org_a.pk),
                    '_save': 'Save',
                },
            ).status_code,
            403,
        )
        self.assertFalse(Team.objects.filter(name='empty-team').exists())
        self._closed(
            self.empty_client,
            reverse('admin:gh_permissions_team_change', args=[self.team_a.pk]),
            {
                'name': 'hijack',
                'organization': str(self.org_a.pk),
                '_save': 'Save',
            },
        )
        self.assertFalse(Team.objects.filter(name='hijack').exists())
        self.empty_client.post(
            reverse('admin:gh_permissions_organization_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '1',
                ACTION_CHECKBOX_NAME: [str(self.org_a.pk), str(self.org_b.pk)],
            },
        )
        self.assertEqual(Organization.objects.count(), 4)

    def test_non_staff_and_ordinary_members_cannot_administer(self):
        for client in (self.member_client, _client_for(self.inactive)):
            response = client.get(reverse('admin:index'))
            self.assertEqual(response.status_code, 302)
            self.assertIn('/admin/login/', response['Location'])
            denied = client.post(
                reverse('admin:gh_permissions_team_delete', args=[self.team_a.pk]),
                {'post': 'yes'},
            )
            self.assertEqual(denied.status_code, 302)
            self.assertIn('/admin/login/', denied['Location'])
        self.assertTrue(Team.objects.filter(pk=self.team_a.pk).exists())
        registry = _registry()
        self.assertFalse(registry.has_permission(self.member, self.repo_a, self.read))
        TeamRepositoryPermission.objects.create(
            team=self.team_a, repository=self.repo_a, operation=self.read,
        )
        self.assertTrue(registry.has_permission(self.member, self.repo_a, self.read))
        self.assertFalse(registry.has_permission(self.owner_a, self.repo_a, self.read))
        self.assertEqual(
            self.owner_client.get(reverse('admin:gh_permissions_operation_changelist')).status_code,
            403,
        )

    def test_deleting_the_owner_clears_administration_and_keeps_rows(self):
        self.owner_a.delete()
        self.org_a.refresh_from_db()
        self.assertIsNone(self.org_a.owner_id)
        self.assertTrue(Team.objects.filter(pk=self.team_a.pk).exists())
        self.assertTrue(Repository.objects.filter(pk=self.repo_a.pk).exists())
        listing = self.super_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertContains(listing, 'owned-alpha')
        self.assertNotContains(
            self.foreign_client.get(
                reverse('admin:gh_permissions_organization_changelist'),
            ),
            'owned-alpha',
        )
