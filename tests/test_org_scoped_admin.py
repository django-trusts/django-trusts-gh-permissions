"""Admin calls the domain services and does not invent owner authority.

Staff owners are not a scope: the ``is_owner`` registration is not
installed. Superusers bypass the scope mixin. ``_admin_scope`` stays
free of GH model nouns.
"""

from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from gh_permissions._admin_scope import AuthorizedScopeAdminMixin
from gh_permissions.admin import (
    OrganizationAdmin,
    OrganizationMembershipAdmin,
    OrgScopedAdmin,
    RepositoryAdmin,
    RepositoryCollaboratorAdmin,
    TeamAdmin,
    TeamRepositoryPermissionAdmin,
)
from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationMembership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import create_organization, create_user
from tests.test_migration_0002 import _ALIAS, _wipe


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
    OrganizationMembershipAdmin,
)
SERVICE_HOOKS = ('save_model', 'delete_model', 'delete_queryset')


def _grant_scoped(user):
    codenames = []
    for model in (
        Organization, Team, Repository, RepositoryCollaborator,
        TeamRepositoryPermission, OrganizationMembership,
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
        self.assertTrue(TeamAdmin.scope_allows_delete)

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
            'OrganizationMembership',
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

    def test_staff_owner_sees_no_organizations(self):
        owner = create_user('ada-owner', is_staff=True)
        _grant_scoped(owner)
        organization = create_organization('acme')
        OrganizationMembership.objects.create(
            user=owner, organization=organization, is_owner=True,
        )
        client = Client()
        client.force_login(owner, backend=ADMIN_BACKENDS[1])
        response = client.get(reverse('admin:gh_permissions_organization_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'acme')
        self.assertContains(response, '0 organizations')
        visible = self.client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertContains(visible, 'acme')
        self.assertContains(visible, 'ada-owner')

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
