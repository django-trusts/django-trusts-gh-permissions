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
from django.core.exceptions import PermissionDenied
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse

from gh_permissions._admin_scope import AuthorizedScopeAdminMixin
from gh_permissions.admin import (
    OrganizationAdmin,
    OrganizationOwnerPermissionAdmin,
    OrgScopedAdmin,
    RepositoryAdmin,
    TeamAdmin,
    TeamRepositoryPermissionAdmin,
    UserRepositoryPermissionAdmin,
)
from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import (
    Organization,
    OrganizationOwnerPermission,
    Repository,
    Team,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)
from tests.fixtures import repository_permission
from tests.test_migration_0002 import _ALIAS, _wipe
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
    OrganizationOwnerPermission,
)
CONCRETE_ADMINS = (
    OrganizationAdmin,
    TeamAdmin,
    RepositoryAdmin,
    UserRepositoryPermissionAdmin,
    TeamRepositoryPermissionAdmin,
    OrganizationOwnerPermissionAdmin,
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
            'has_change_permission',
            'has_delete_permission',
            'has_add_permission',
            'formfield_for_foreignkey',
            'formfield_for_manytomany',
            'save_model',
            'get_actions',
            'check',
        )
        for admin_cls in CONCRETE_ADMINS:
            self.assertTrue(issubclass(admin_cls, OrgScopedAdmin))
            self.assertTrue(issubclass(admin_cls, admin.ModelAdmin))
            self.assertTrue(issubclass(admin_cls, AuthorizedScopeAdminMixin))
            for hook in hooks:
                self.assertIn(hook, AuthorizedScopeAdminMixin.__dict__)
                self.assertNotIn(hook, admin_cls.__dict__)
                self.assertNotIn(hook, OrgScopedAdmin.__dict__)
            self.assertIn('authorization_scope_paths', admin_cls.__dict__)
            self.assertEqual(admin_cls.list_filter, ())
            self.assertEqual(admin_cls.search_fields, ())
            self.assertEqual(admin_cls.autocomplete_fields, ())
            self.assertEqual(admin_cls.raw_id_fields, ())
            self.assertEqual(admin_cls.inlines, ())
            self.assertEqual(admin_cls.list_editable, ())
        self.assertNotIn('get_form', AuthorizedScopeAdminMixin.__dict__)
        self.assertNotIn('has_view_permission', AuthorizedScopeAdminMixin.__dict__)

    def test_auth_permission_is_not_organization_scoped(self):
        registered = admin.site._registry[Permission]
        self.assertNotIsInstance(registered, OrgScopedAdmin)
        self.assertNotIsInstance(registered, AuthorizedScopeAdminMixin)

    def test_scope_module_has_no_gh_nouns_or_trusts_calls(self):
        source = (ROOT / 'gh_permissions' / '_admin_scope.py').read_text()
        for noun in (
            'Organization',
            'Operation',
            'Repository',
            'Team',
            'OrganizationOwnerPermission',
            'UserRepositoryPermission',
            'TeamRepositoryPermission',
        ):
            self.assertNotIn(noun, source)
        self.assertNotIn('trusts', source)
        self.assertNotIn('.registry', source)
        self.assertNotIn('compiler', source)

    def test_unsupported_surfaces_fail_checks(self):
        admin_obj = TeamAdmin(Team, admin.site)
        admin_obj.list_editable = ('name',)
        errors = admin_obj.check()
        self.assertIn('admin_scope.E001', [error.id for error in errors])

    def test_no_custom_admin_templates_or_views(self):
        self.assertFalse((ROOT / 'gh_permissions' / 'templates').exists())
        self.assertFalse((ROOT / 'gh_permissions' / 'views.py').exists())

    def test_owner_migration_creates_the_grant_and_not_an_owner_column(self):
        text = (
            ROOT / 'gh_permissions' / 'migrations'
            / '0003_organization_owner_permission.py'
        ).read_text()
        self.assertIn("name='OrganizationOwnerPermission'", text)
        self.assertIn('0002_auth_permission_terminal', text)
        self.assertIn('manage_organization', text)
        self.assertNotIn('AddField', text)
        self.assertNotIn('RemoveField', text)
        self.assertNotIn('DeleteModel', text)
        self.assertNotIn('RunPython', text)
        self.assertNotIn('owned_organizations', text)
        admin_source = (ROOT / 'gh_permissions' / 'admin.py').read_text()
        self.assertIn('Organization.objects.authorized', admin_source)
        self.assertNotIn('_scope_queryset', admin_source)
        self.assertNotIn('owner=user', admin_source)
        self.assertNotIn('owner_id', admin_source)

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
    databases = {'default', _ALIAS}

    def test_upgrade_from_initial_preserves_rows_without_an_owner_column(self):
        reset = connections[_ALIAS]
        _wipe(reset)
        executor = MigrationExecutor(reset)
        executor.migrate([('gh_permissions', '0001_initial')])
        with reset.cursor() as cursor:
            columns = {
                column.name
                for column in reset.introspection.get_table_description(
                    cursor, 'gh_permissions_organization',
                )
            }
        self.assertNotIn('owner_id', columns)
        with reset.cursor() as cursor:
            cursor.execute(
                "INSERT INTO gh_permissions_organization (name) "
                "VALUES ('legacy-org')"
            )
        executor.loader.build_graph()
        executor.migrate([
            ('gh_permissions', '0003_organization_owner_permission'),
        ])
        apps = executor.loader.project_state(
            ('gh_permissions', '0003_organization_owner_permission'),
        ).apps
        HistoricalOrganization = apps.get_model('gh_permissions', 'Organization')
        HistoricalGrant = apps.get_model(
            'gh_permissions', 'OrganizationOwnerPermission',
        )
        org = HistoricalOrganization.objects.using(_ALIAS).get(name='legacy-org')
        self.assertEqual(HistoricalOrganization.objects.using(_ALIAS).count(), 1)
        self.assertFalse(
            any(field.name == 'owner' for field in HistoricalOrganization._meta.local_fields),
        )
        self.assertFalse(
            HistoricalGrant.objects.using(_ALIAS).filter(organization_id=org.pk).exists(),
        )
        self.assertEqual(HistoricalGrant.objects.using(_ALIAS).count(), 0)


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
        self.org_a = Organization.objects.create(name='owned-alpha')
        self.org_a2 = Organization.objects.create(name='owned-delta')
        self.org_b = Organization.objects.create(name='foreign-beta')
        self.org_free = Organization.objects.create(name='unowned-gamma')
        self.team_a = Team.objects.create(organization=self.org_a, name='alpha-writers')
        self.team_b = Team.objects.create(organization=self.org_b, name='beta-outsiders')
        self.team_a.members.add(self.member)
        self.read = repository_permission('read_repository')
        self.write = repository_permission('write_repository')
        self.manage = Permission.objects.get(
            content_type__app_label='gh_permissions',
            content_type__model='organization',
            codename=OrgScopedAdmin.management_codename,
        )
        self.grant_a = OrganizationOwnerPermission.objects.create(
            organization=self.org_a, owner=self.owner_a, operation=self.manage,
        )
        self.grant_a2 = OrganizationOwnerPermission.objects.create(
            organization=self.org_a2, owner=self.owner_a, operation=self.manage,
        )
        self.grant_b = OrganizationOwnerPermission.objects.create(
            organization=self.org_b, owner=self.owner_b, operation=self.manage,
        )
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
            self.super_client.get(reverse('admin:auth_permission_changelist')).status_code,
            200,
        )
        self.assertEqual(
            self.super_client.get(
                reverse('admin:example_user_change', args=[self.target.pk]),
            ).status_code,
            200,
        )
        created = self.super_client.post(
            reverse('admin:gh_permissions_organization_add'),
            {'name': 'super-added', '_save': 'Save'},
        )
        self.assertEqual(created.status_code, 302)
        added = Organization.objects.get(name='super-added')
        self.assertFalse(
            OrganizationOwnerPermission.objects.filter(organization=added).exists(),
        )
        granted = self.super_client.post(
            reverse('admin:gh_permissions_organizationownerpermission_add'),
            {
                'organization': str(added.pk),
                'owner': str(self.owner_a.pk),
                'operation': str(self.manage.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(granted.status_code, 302)
        self.assertContains(
            self.owner_client.get(
                reverse('admin:gh_permissions_organization_changelist'),
            ),
            'super-added',
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
                self.assertIn('gh_permissions_organizationownerpermission', sql)
                self.assertNotIn('gh_permissions_organization"."owner_id', sql)
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
        self.assertTrue(
            OrganizationOwnerPermission.objects.filter(
                pk=self.grant_b.pk, owner=self.owner_b, organization=self.org_b,
            ).exists(),
        )
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
                reverse('admin:example_user_change', args=[self.owner_b.pk]),
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

    def test_owner_cannot_mint_or_retarget_the_authority_grant(self):
        renamed = self.owner_client.post(
            reverse('admin:gh_permissions_organization_change', args=[self.org_a.pk]),
            {'name': 'owned-alpha-renamed', '_save': 'Save'},
        )
        self.assertEqual(renamed.status_code, 302)
        self.org_a.refresh_from_db()
        self.assertEqual(self.org_a.name, 'owned-alpha-renamed')
        self.grant_a.refresh_from_db()
        self.assertEqual(self.grant_a.owner_id, self.owner_a.pk)
        add_org = self.owner_client.post(
            reverse('admin:gh_permissions_organization_add'),
            {'name': 'stolen', '_save': 'Save'},
        )
        self.assertEqual(add_org.status_code, 403)
        self.assertFalse(Organization.objects.filter(name='stolen').exists())
        forged_add = self.owner_client.post(
            reverse('admin:gh_permissions_organizationownerpermission_add'),
            {
                'organization': str(self.org_free.pk),
                'owner': str(self.owner_a.pk),
                'operation': str(self.manage.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(forged_add.status_code, 403)
        self.assertFalse(
            OrganizationOwnerPermission.objects.filter(organization=self.org_free).exists(),
        )
        forged_change = self.owner_client.post(
            reverse(
                'admin:gh_permissions_organizationownerpermission_change',
                args=[self.grant_a.pk],
            ),
            {
                'organization': str(self.org_b.pk),
                'owner': str(self.owner_a.pk),
                'operation': str(self.manage.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(forged_change.status_code, 403)
        self.grant_a.refresh_from_db()
        self.assertEqual(self.grant_a.organization_id, self.org_a.pk)
        self.assertEqual(self.grant_a.owner_id, self.owner_a.pk)
        self.assertEqual(self.grant_a.operation_id, self.manage.pk)
        forged_delete = self.owner_client.post(
            reverse(
                'admin:gh_permissions_organizationownerpermission_delete',
                args=[self.grant_a.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(forged_delete.status_code, 403)
        bulk = self.owner_client.post(
            reverse('admin:gh_permissions_organizationownerpermission_changelist'),
            {
                'action': 'delete_selected',
                'post': 'yes',
                'select_across': '1',
                ACTION_CHECKBOX_NAME: [str(self.grant_a.pk)],
            },
        )
        self.assertEqual(bulk.status_code, 403)
        self.assertTrue(
            OrganizationOwnerPermission.objects.filter(pk=self.grant_a.pk).exists(),
        )
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
            reverse(
                'admin:gh_permissions_organizationownerpermission_change',
                args=[self.grant_b.pk],
            ),
            {
                'organization': str(self.org_b.pk),
                'owner': str(self.owner_a.pk),
                'operation': str(self.manage.pk),
                '_save': 'Save',
            },
        )
        self.assertEqual(reassigned.status_code, 302)
        self.grant_b.refresh_from_db()
        self.assertEqual(self.grant_b.owner_id, self.owner_a.pk)
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
            self.owner_client.get(reverse('admin:auth_permission_changelist')).status_code,
            403,
        )

    def test_deleting_the_owner_clears_administration_and_keeps_rows(self):
        self.owner_a.delete()
        self.assertFalse(
            OrganizationOwnerPermission.objects.filter(organization=self.org_a).exists(),
        )
        self.assertTrue(Organization.objects.filter(pk=self.org_a.pk).exists())
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

    def test_owner_grant_row_gates_admin_and_matches_authorized(self):
        OrganizationOwnerPermission.objects.filter(owner=self.owner_a).delete()
        self.assertEqual(
            set(Organization.objects.authorized(self.owner_a, self.manage)),
            set(),
        )
        hidden = self.owner_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertNotContains(hidden, 'owned-alpha')
        self.assertNotContains(hidden, 'owned-delta')
        self._closed(
            self.owner_client,
            reverse('admin:gh_permissions_organization_change', args=[self.org_a.pk]),
        )

        OrganizationOwnerPermission.objects.create(
            organization=self.org_a, owner=self.owner_a, operation=self.manage,
        )
        managed = set(Organization.objects.authorized(self.owner_a, self.manage))
        self.assertEqual(managed, {self.org_a})
        shown = self.owner_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertContains(shown, 'owned-alpha')
        self.assertNotContains(shown, 'owned-delta')
        self.assertNotContains(shown, 'foreign-beta')
        self.assertNotContains(shown, 'unowned-gamma')
        self.assertEqual(
            self.owner_client.get(
                reverse('admin:gh_permissions_team_change', args=[self.team_a.pk]),
            ).status_code,
            200,
        )

        grant = OrganizationOwnerPermission.objects.get(organization=self.org_a)
        grant.operation = self.read
        grant.save()
        self.assertEqual(
            set(Organization.objects.authorized(self.owner_a, self.manage)),
            set(),
        )
        self._closed(
            self.owner_client,
            reverse('admin:gh_permissions_organization_change', args=[self.org_a.pk]),
        )

        grant.operation = self.manage
        grant.owner = self.owner_b
        grant.save()
        self.assertNotIn(
            self.org_a, Organization.objects.authorized(self.owner_a, self.manage),
        )
        self.assertIn(
            self.org_a, Organization.objects.authorized(self.owner_b, self.manage),
        )
        self.assertContains(
            self.foreign_client.get(
                reverse('admin:gh_permissions_organization_changelist'),
            ),
            'owned-alpha',
        )
        self._closed(
            self.owner_client,
            reverse('admin:gh_permissions_organization_change', args=[self.org_a.pk]),
        )
        grant.delete()
        self.assertNotIn(
            self.org_a, Organization.objects.authorized(self.owner_b, self.manage),
        )
        self._closed(
            self.foreign_client,
            reverse('admin:gh_permissions_organization_change', args=[self.org_a.pk]),
        )

    def test_repository_paths_do_not_supply_admin_authority(self):
        registry = _registry()
        with self.assertNumQueries(1):
            self.assertFalse(
                registry.has_permission(self.owner_a, self.repo_a, self.read),
            )
        self.assertEqual(
            list(Repository.objects.authorized(self.owner_a, self.read)),
            [],
        )
        self.assertEqual(
            set(Organization.objects.authorized(self.owner_a, self.manage)),
            {self.org_a, self.org_a2},
        )
        self.team_a.members.add(self.staff_empty)
        UserRepositoryPermission.objects.create(
            user=self.staff_empty, repository=self.repo_a, operation=self.read,
        )
        TeamRepositoryPermission.objects.create(
            team=self.team_a, repository=self.repo_a, operation=self.read,
        )
        self.assertTrue(
            registry.has_permission(self.staff_empty, self.repo_a, self.read),
        )
        self.assertEqual(
            list(Organization.objects.authorized(self.staff_empty, self.manage)),
            [],
        )
        listing = self.empty_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, 'owned-alpha')
        self.assertEqual(
            self.empty_client.get(
                reverse('admin:gh_permissions_team_change', args=[self.team_a.pk]),
            ).status_code,
            302,
        )

    def test_missing_management_operation_authorizes_nothing(self):
        previous = OrgScopedAdmin.management_codename
        OrgScopedAdmin.management_codename = 'missing_manage'
        try:
            self.assertEqual(
                set(Organization.objects.authorized(self.owner_a, self.manage)),
                {self.org_a, self.org_a2},
            )
            listing = self.owner_client.get(
                reverse('admin:gh_permissions_organization_changelist'),
            )
            self.assertNotContains(listing, 'owned-alpha')
            self.assertEqual(
                self.owner_client.get(
                    reverse('admin:gh_permissions_team_add'),
                ).status_code,
                403,
            )
        finally:
            OrgScopedAdmin.management_codename = previous

    def test_save_model_rejects_an_out_of_scope_instance(self):
        request = RequestFactory().post('/')
        request.user = self.owner_a
        team = Team(organization=self.org_b, name='smuggled-save')
        with self.assertRaises(PermissionDenied):
            admin.site._registry[Team].save_model(
                request, team, form=None, change=False,
            )
        self.assertFalse(Team.objects.filter(name='smuggled-save').exists())
