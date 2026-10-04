"""Admin calls the domain services and scopes by ownership rows.

An ``OrganizationOwnership`` row is the owner grant. Staff owners see
those organizations. A team member with no ownership row does not.
Non-superusers cannot add, change, or delete ownership rows.
Superusers bypass the scope mixin. ``_admin_scope`` stays free of GH
model nouns.
"""

from html.parser import HTMLParser
from pathlib import Path

from django.contrib import admin
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from django.utils import timezone

from gh_permissions._admin_scope import AuthorizedScopeAdminMixin
from gh_permissions.admin import (
    OrganizationAdmin,
    OrganizationOwnershipAdmin,
    OrgScopedAdmin,
    RepositoryAdmin,
    RepositoryCollaboratorAdmin,
    ServiceBackedUserAdmin,
    TeamAdmin,
    TeamRepositoryPermissionAdmin,
)
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
from gh_permissions.services import create_organization, create_user
from tests.fixtures import repository_permission
from tests.test_migration_0002 import _ALIAS, _wipe
from tests.test_org_scoped_requests import OrgScopedAdminRequestTests
from trusts.apps import implementation_for_path


ROOT = Path(__file__).resolve().parents[1]
ADMIN_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
    'django.contrib.auth.backends.ModelBackend',
)
CONCRETE_ADMINS = (
    OrganizationAdmin,
    TeamAdmin,
    RepositoryAdmin,
    RepositoryCollaboratorAdmin,
    TeamRepositoryPermissionAdmin,
    OrganizationOwnershipAdmin,
)
SERVICE_HOOKS = ('save_model', 'delete_model', 'delete_queryset')


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


def _client_for(user):
    client = Client()
    client.force_login(user, backend=ADMIN_BACKENDS[1])
    return client


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
                if admin_cls is OrganizationAdmin and hook == 'save_model':
                    self.assertIn(hook, admin_cls.__dict__)
                else:
                    self.assertNotIn(hook, admin_cls.__dict__)
                self.assertNotIn(hook, OrgScopedAdmin.__dict__)
            self.assertIn('authorization_scope_paths', admin_cls.__dict__)
            self.assertEqual(admin_cls.list_filter, ())
            self.assertEqual(admin_cls.search_fields, ())
            self.assertEqual(admin_cls.autocomplete_fields, ())
            self.assertEqual(admin_cls.raw_id_fields, ())
            self.assertEqual(admin_cls.inlines, ())
            self.assertEqual(admin_cls.list_editable, ())
        for hook in SERVICE_HOOKS:
            self.assertIn(hook, OrganizationAdmin.__dict__)
            self.assertNotIn(hook, TeamAdmin.__dict__)
        self.assertNotIn('get_form', AuthorizedScopeAdminMixin.__dict__)
        self.assertNotIn('has_view_permission', AuthorizedScopeAdminMixin.__dict__)
        self.assertFalse(OrganizationAdmin.scope_allows_add)
        self.assertFalse(OrganizationOwnershipAdmin.scope_allows_add)
        self.assertFalse(OrganizationOwnershipAdmin.scope_allows_change)
        self.assertFalse(OrganizationOwnershipAdmin.scope_allows_delete)
        self.assertTrue(TeamAdmin.scope_allows_delete)

    def test_example_user_admin_calls_the_shared_name_services(self):
        registered = admin.site._registry[get_user_model()]
        self.assertIsInstance(registered, ServiceBackedUserAdmin)
        self.assertNotEqual(type(registered), UserAdmin)
        admin_source = (ROOT / 'gh_permissions' / 'admin.py').read_text()
        self.assertIn('create_user', admin_source)
        self.assertIn('rename_user', admin_source)
        self.assertIn('delete_user', admin_source)

    def test_auth_permission_is_not_organization_scoped(self):
        registered = admin.site._registry[Permission]
        self.assertNotIsInstance(registered, OrgScopedAdmin)
        self.assertNotIsInstance(registered, AuthorizedScopeAdminMixin)

    def test_alias_is_not_registered_in_admin(self):
        self.assertNotIn(Alias, admin.site._registry)

    def test_scope_module_has_no_gh_nouns_or_trusts_calls(self):
        source = (ROOT / 'gh_permissions' / '_admin_scope.py').read_text()
        for noun in (
            'Organization',
            'Operation',
            'Repository',
            'Team',
            'Alias',
            'OrganizationOwnership',
            'RepositoryCollaborator',
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
        self.assertIn('create_organization', admin_source)
        self.assertIn('rename_organization', admin_source)
        self.assertIn('delete_organization', admin_source)
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
class OrganizationAdminServiceTests(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username='root', email='root@example.com', password='secret',
        )
        self.client = Client()
        self.client.force_login(self.superuser, backend=ADMIN_BACKENDS[1])

    def test_superuser_add_reserves_the_alias(self):
        response = self.client.post(
            reverse('admin:gh_permissions_organization_add'),
            {'name': 'acme'},
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        organization = Organization.objects.get(name='acme')
        self.assertIsNone(organization.personal_user_id)
        self.assertEqual(organization.owner_group.name, 'organization-owners')
        self.assertTrue(Alias.objects.filter(name='acme').exists())

    def test_blank_conventional_name_stays_on_the_form(self):
        response = self.client.post(
            reverse('admin:gh_permissions_organization_add'),
            {'name': '   '},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'needs a name')
        self.assertFalse(Organization.objects.filter(personal_user__isnull=True).exists())

    def test_personal_organization_change_does_not_assign_a_name(self):
        user = create_user('ada')
        organization = user.personal_organization
        response = self.client.post(
            reverse(
                'admin:gh_permissions_organization_change',
                args=[organization.pk],
            ),
            {'name': 'renamed'},
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        organization.refresh_from_db()
        user.refresh_from_db()
        self.assertIsNone(organization.name)
        self.assertEqual(user.username, 'ada')
        self.assertTrue(Alias.objects.filter(name='ada').exists())
        self.assertFalse(Alias.objects.filter(name='renamed').exists())

    def test_bulk_delete_releases_the_alias(self):
        organization = create_organization('acme')
        changelist = reverse('admin:gh_permissions_organization_changelist')
        response = self.client.post(changelist, {
            'action': 'delete_selected',
            'post': 'yes',
            '_selected_action': [str(organization.pk)],
        })
        self.assertEqual(response.status_code, 302, response.content[:500])
        self.assertFalse(Organization.objects.filter(name='acme').exists())
        self.assertFalse(Alias.objects.filter(name='acme').exists())

    def test_duplicate_name_stays_on_the_form(self):
        create_organization('acme')
        response = self.client.post(
            reverse('admin:gh_permissions_organization_add'),
            {'name': 'acme'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already reserved')
        self.assertEqual(Organization.objects.filter(name='acme').count(), 1)

    def test_superuser_rename_and_delete_follow_the_alias(self):
        organization = create_organization('old-name')
        response = self.client.post(
            reverse(
                'admin:gh_permissions_organization_change',
                args=[organization.pk],
            ),
            {'name': 'new-name'},
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        organization.refresh_from_db()
        self.assertEqual(organization.name, 'new-name')
        self.assertFalse(Alias.objects.filter(name='old-name').exists())
        self.assertTrue(Alias.objects.filter(name='new-name').exists())

        response = self.client.post(
            reverse(
                'admin:gh_permissions_organization_delete',
                args=[organization.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        self.assertFalse(Organization.objects.filter(name='new-name').exists())
        self.assertFalse(Alias.objects.filter(name='new-name').exists())

    def test_deleting_a_personal_organization_deletes_the_user(self):
        user = create_user('ada')
        organization = user.personal_organization
        response = self.client.post(
            reverse(
                'admin:gh_permissions_organization_delete',
                args=[organization.pk],
            ),
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302, response.content[:500])
        self.assertFalse(get_user_model().objects.filter(username='ada').exists())
        self.assertFalse(Organization.objects.filter(pk=organization.pk).exists())
        self.assertFalse(Alias.objects.filter(name='ada').exists())

    def test_staff_owner_sees_only_owned_organizations(self):
        owner = create_user('ada-owner', is_staff=True)
        _grant_scoped(owner)
        organization = create_organization('acme')
        other = create_organization('other')
        OrganizationOwnership.objects.create(
            user=owner, organization=organization,
        )
        team = Team.objects.create(organization=other, name='writers')
        team.members.add(owner)
        client = Client()
        client.force_login(owner, backend=ADMIN_BACKENDS[1])
        response = client.get(reverse('admin:gh_permissions_organization_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'acme')
        self.assertContains(response, 'ada-owner')
        self.assertNotContains(response, 'other')
        visible = self.client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertContains(visible, 'acme')
        self.assertContains(visible, 'ada-owner')
        self.assertContains(visible, 'other')

    def test_staff_save_is_still_rejected_by_the_scope_mixin(self):
        staff = create_user('staff', is_staff=True)
        request = RequestFactory().post('/')
        request.user = staff
        model_admin = OrganizationAdmin(Organization, admin.site)
        organization = create_organization('acme')
        with self.assertRaises(PermissionDenied):
            model_admin.save_model(request, organization, None, change=True)
        fresh = Organization(name='other')
        with self.assertRaises(PermissionDenied):
            model_admin.save_model(request, fresh, None, change=False)
        organization.refresh_from_db()
        self.assertEqual(organization.name, 'acme')

    def test_superuser_user_admin_keeps_the_alias_and_personal_organization(self):
        response = self.client.post(
            reverse('admin:example_user_add'),
            {
                'username': 'ada',
                'usable_password': 'true',
                'password1': 'secret-pass-1',
                'password2': 'secret-pass-1',
                '_save': 'Save',
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:800])
        user = get_user_model().objects.get(username='ada')
        self.assertTrue(user.check_password('secret-pass-1'))
        self.assertTrue(Alias.objects.filter(name='ada').exists())
        self.assertIsNone(user.personal_organization.name)
        self.assertTrue(OrganizationOwnership.objects.filter(
            user=user, organization=user.personal_organization,
        ).exists())

        joined = timezone.localtime(user.date_joined)
        response = self.client.post(
            reverse('admin:example_user_change', args=[user.pk]),
            {
                'username': 'ada-renamed',
                'first_name': '',
                'last_name': '',
                'email': '',
                'is_active': 'on',
                'date_joined_0': joined.strftime('%Y-%m-%d'),
                'date_joined_1': joined.strftime('%H:%M:%S'),
                '_save': 'Save',
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:800])
        user.refresh_from_db()
        self.assertEqual(user.username, 'ada-renamed')
        self.assertFalse(Alias.objects.filter(name='ada').exists())
        self.assertTrue(Alias.objects.filter(name='ada-renamed').exists())

        response = self.client.post(
            reverse('admin:example_user_delete', args=[user.pk]),
            {'post': 'yes'},
        )
        self.assertEqual(response.status_code, 302, response.content[:800])
        self.assertFalse(get_user_model().objects.filter(pk=user.pk).exists())
        self.assertFalse(Alias.objects.filter(name='ada-renamed').exists())
        self.assertFalse(Organization.objects.filter(personal_user_id=user.pk).exists())

    def test_user_admin_rejects_a_name_held_by_an_organization(self):
        create_organization('acme')
        response = self.client.post(
            reverse('admin:example_user_add'),
            {
                'username': 'acme',
                'usable_password': 'true',
                'password1': 'secret-pass-1',
                'password2': 'secret-pass-1',
                '_save': 'Save',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already reserved')
        self.assertFalse(get_user_model().objects.filter(username='acme').exists())
