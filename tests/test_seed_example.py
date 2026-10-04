"""Example-project seed_example command on the shared-name schema.

A freshly migrated test database, then a second run. Users and the
conventional organization go through gh_permissions.services. The
command does not change gh_permissions behavior.
"""

from io import StringIO
from pathlib import Path

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import CommandError, call_command, get_commands
from django.db import connection
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse

from example.management.commands.seed_example import (
    CEILING_CODENAMES,
    DEFAULT_ORGANIZATION_NAME,
    DEVELOPMENT_ONLY_NOTICE,
    DEVELOPMENT_PASSWORDS,
    DIRECT_USERNAME,
    EXCLUDED_CODENAME,
    OUTSIDER_USERNAME,
    OWNER_USERNAME,
    REPOSITORY_TITLE,
    SUPERUSER_USERNAME,
    TEAM_NAME,
    TEAM_USERNAME,
    USER_SPECS,
    Command,
)
from gh_permissions.admin import OrganizationAdmin, RepositoryAdmin, TeamAdmin
from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import (
    OWNER_GROUP_NAME,
    OWNER_PERMISSIONS,
    create_organization,
)


ROOT = Path(__file__).resolve().parents[1]
ADMIN_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
    'django.contrib.auth.backends.ModelBackend',
)
REPOSITORY_CASES = (
    ('read_repository', {DIRECT_USERNAME, TEAM_USERNAME, OWNER_USERNAME}),
    ('write_repository', {DIRECT_USERNAME, OWNER_USERNAME}),
    ('admin_repository', {OWNER_USERNAME}),
)
SCOPED_MODELS = (
    Organization,
    Team,
    Repository,
    RepositoryCollaborator,
    TeamRepositoryPermission,
    OrganizationOwnership,
)


def _permission(model, codename):
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model=model,
        codename=codename,
    )


def _write_sql(captured):
    writes = []
    for query in captured:
        sql = query['sql'].lstrip().upper()
        if sql.startswith(('INSERT', 'UPDATE', 'DELETE', 'REPLACE')):
            writes.append(sql)
    return writes


def _request_for(user):
    request = RequestFactory().get('/admin/')
    request.user = user
    return request


class SeedExampleCommandTests(TestCase):
    def _assert_repository_inquiries(self, organization_name):
        User = get_user_model()
        organization = Organization.objects.get(name=organization_name)
        repository = Repository.objects.get(
            organization=organization, name=REPOSITORY_TITLE,
        )
        users = {
            username: User.objects.get(username=username)
            for username in DEVELOPMENT_PASSWORDS
        }
        superuser = users[SUPERUSER_USERNAME]
        self.assertTrue(superuser.is_active)
        self.assertTrue(superuser.is_superuser)
        ordinary = list(User.objects.filter(is_superuser=False, is_active=True))
        self.assertGreaterEqual(len(ordinary), 4)

        for codename, expected_ordinary in REPOSITORY_CASES:
            permission = _permission('repository', codename)
            code = 'gh_permissions.%s' % codename
            via_manager = set(User.objects.permitted(repository, code))
            via_manager_row = set(User.objects.permitted(repository, permission))
            via_content = set(repository.get_permitted_users(code))
            via_content_row = set(repository.get_permitted_users(permission))
            self.assertEqual(via_manager, via_manager_row)
            self.assertEqual(via_manager, via_content)
            self.assertEqual(via_manager, via_content_row)
            self.assertIn(superuser, via_manager)
            self.assertEqual(
                {user.username for user in via_manager if not user.is_superuser},
                expected_ordinary,
            )
            self.assertNotIn(
                repository,
                list(Repository.objects.authorized(superuser, permission)),
            )
            self.assertTrue(superuser.has_perm(code, repository))
            for user in ordinary:
                granted = user.has_perm(code, repository)
                listed = repository in list(
                    Repository.objects.authorized(user, permission)
                )
                self.assertEqual(listed, granted)
                self.assertEqual(user in via_manager, granted)
                self.assertEqual(user in via_content, granted)

        team = Team.objects.get(organization=organization, name=TEAM_NAME)
        self.assertEqual(tuple(CEILING_CODENAMES), ('read_repository',))
        self.assertFalse(
            team.allowed_operations.filter(
                content_type__app_label='gh_permissions',
                content_type__model='repository',
                codename=EXCLUDED_CODENAME,
            ).exists()
        )
        self.assertTrue(
            TeamRepositoryPermission.objects.filter(
                team=team,
                repository=repository,
                operation__content_type__app_label='gh_permissions',
                operation__content_type__model='repository',
                operation__codename=EXCLUDED_CODENAME,
            ).exists()
        )
        team_user = users[TEAM_USERNAME]
        direct = users[DIRECT_USERNAME]
        owner = users[OWNER_USERNAME]
        self.assertFalse(
            team_user.has_perm('gh_permissions.write_repository', repository)
        )
        self.assertNotIn(
            repository,
            list(Repository.objects.authorized(
                team_user, _permission('repository', EXCLUDED_CODENAME),
            )),
        )
        outsider = users[OUTSIDER_USERNAME]
        read = _permission('repository', 'read_repository')
        self.assertFalse(
            outsider.has_perm('gh_permissions.read_repository', repository)
        )
        self.assertNotIn(
            repository,
            list(Repository.objects.authorized(outsider, read)),
        )
        self.assertFalse(direct.teams.exists())
        self.assertFalse(
            RepositoryCollaborator.objects.filter(user=team_user).exists()
        )
        self.assertFalse(
            RepositoryCollaborator.objects.filter(user=owner).exists()
        )
        collaboration = RepositoryCollaborator.objects.get(
            user=direct, repository=repository,
        )
        self.assertEqual(
            set(collaboration.permissions.values_list(
                'content_type__app_label',
                'content_type__model',
                'codename',
            )),
            {
                ('gh_permissions', 'repository', 'read_repository'),
                ('gh_permissions', 'repository', 'write_repository'),
            },
        )
        return organization, repository, users

    def _assert_owner_scope(self, organization, repository, users):
        owner = users[OWNER_USERNAME]
        manage = _permission('organization', 'manage_organization')
        self.assertTrue(owner.is_staff)
        self.assertFalse(owner.is_superuser)
        self.assertEqual(
            set(Organization.objects.authorized(owner, manage)),
            {organization, owner.personal_organization},
        )
        for username in (DIRECT_USERNAME, TEAM_USERNAME, OUTSIDER_USERNAME):
            user = users[username]
            self.assertEqual(
                set(Organization.objects.authorized(user, manage)),
                {user.personal_organization},
            )
            self.assertFalse(
                user.has_perm('gh_permissions.manage_organization', organization)
            )
        superuser = users[SUPERUSER_USERNAME]
        self.assertEqual(
            set(Organization.objects.authorized(superuser, manage)),
            {superuser.personal_organization},
        )
        self.assertNotIn(
            organization,
            set(Organization.objects.authorized(superuser, manage)),
        )
        self.assertTrue(
            owner.has_perm('gh_permissions.manage_organization', organization)
        )
        self.assertTrue(
            superuser.has_perm('gh_permissions.manage_organization', organization)
        )
        self.assertTrue(owner.has_perm('gh_permissions.read_repository', repository))
        self.assertTrue(owner.has_perm('gh_permissions.admin_repository', repository))
        self.assertFalse(
            users[DIRECT_USERNAME].has_perm(
                'gh_permissions.admin_repository', repository,
            )
        )
        grant = OrganizationOwnership.objects.get(
            user=owner, organization=organization,
        )
        self.assertEqual(grant.organization.owner_group.name, OWNER_GROUP_NAME)
        self.assertEqual(
            set(grant.organization.owner_group.permissions.values_list(
                'content_type__model', 'codename',
            )),
            set(OWNER_PERMISSIONS),
        )
        self.assertTrue(Alias.objects.filter(name=owner.username).exists())
        self.assertTrue(Alias.objects.filter(name=organization.name).exists())
        self.assertIsNone(owner.personal_organization.name)
        self.assertEqual(
            list(owner.personal_organization.owners.all()),
            [owner],
        )

        expected_keys = set()
        for model in SCOPED_MODELS:
            for action in ('view', 'add', 'change', 'delete'):
                expected_keys.add((
                    model._meta.app_label,
                    model._meta.model_name,
                    '%s_%s' % (action, model._meta.model_name),
                ))
        actual_keys = set(owner.user_permissions.values_list(
            'content_type__app_label',
            'content_type__model',
            'codename',
        ))
        self.assertEqual(actual_keys, expected_keys)
        self.assertFalse(users[DIRECT_USERNAME].user_permissions.exists())

        owner_request = _request_for(owner)
        self.assertEqual(
            set(OrganizationAdmin(Organization, admin.site).get_queryset(owner_request)),
            {organization, owner.personal_organization},
        )
        self.assertEqual(
            set(RepositoryAdmin(Repository, admin.site).get_queryset(owner_request)),
            {repository},
        )
        self.assertEqual(
            set(TeamAdmin(Team, admin.site).get_queryset(owner_request)),
            {Team.objects.get(organization=organization, name=TEAM_NAME)},
        )
        outsider = users[OUTSIDER_USERNAME]
        outsider_request = _request_for(outsider)
        self.assertEqual(
            set(OrganizationAdmin(Organization, admin.site).get_queryset(outsider_request)),
            {outsider.personal_organization},
        )

    def test_fresh_database_and_second_run(self):
        User = get_user_model()
        stranger = User.objects.create_user('stranger', password='unrelated-secret')
        other = create_organization('Unrelated Org')
        other_repo = Repository.objects.create(organization=other, name='foreign-repo')
        other_team = Team.objects.create(organization=other, name='foreign-team')
        stranger_hash = stranger.password
        other_group_id = other.owner_group_id

        out = StringIO()
        call_command('seed_example', stdout=out)
        text = out.getvalue()
        self.assertIn('Status: created', text)
        self.assertIn(DEVELOPMENT_ONLY_NOTICE, text)
        self.assertIn('not for production', text)
        self.assertIn(DEFAULT_ORGANIZATION_NAME, text)
        self.assertIn('RepositoryCollaborator', text)
        self.assertIn('OrganizationOwnership', text)
        self.assertIn(EXCLUDED_CODENAME, text)
        for spec in USER_SPECS:
            self.assertIn(spec['username'], text)
            self.assertIn(spec['password'], text)

        organization, repository, users = self._assert_repository_inquiries(
            DEFAULT_ORGANIZATION_NAME,
        )
        self._assert_owner_scope(organization, repository, users)
        read = _permission('repository', 'read_repository')
        self.assertNotIn(
            other_repo,
            list(Repository.objects.authorized(users[TEAM_USERNAME], read)),
        )
        super_request = _request_for(users[SUPERUSER_USERNAME])
        visible = set(
            OrganizationAdmin(Organization, admin.site).get_queryset(super_request)
        )
        self.assertIn(organization, visible)
        self.assertIn(other, visible)
        self.assertEqual(other_team.members.count(), 0)
        self.assertEqual(list(stranger.teams.all()), [])
        self.assertFalse(Alias.objects.filter(name='stranger').exists())
        stranger.refresh_from_db()
        self.assertEqual(stranger.password, stranger_hash)
        self.assertTrue(stranger.check_password('unrelated-secret'))
        other.refresh_from_db()
        self.assertEqual(other.owner_group_id, other_group_id)
        self.assertEqual(other.name, 'Unrelated Org')

        hashes = {user.username: user.password for user in User.objects.all()}
        counts = (
            User.objects.count(),
            Organization.objects.count(),
            Team.objects.count(),
            Repository.objects.count(),
            RepositoryCollaborator.objects.count(),
            TeamRepositoryPermission.objects.count(),
            OrganizationOwnership.objects.count(),
            Alias.objects.count(),
        )
        self.assertEqual(counts, (6, 7, 2, 2, 1, 2, 6, 7))

        again = StringIO()
        with CaptureQueriesContext(connection) as captured:
            call_command(
                'seed_example',
                '--organization-name',
                DEFAULT_ORGANIZATION_NAME,
                stdout=again,
            )
        self.assertEqual(_write_sql(captured), [])
        self.assertIn('Status: reused', again.getvalue())
        self.assertEqual(
            (
                User.objects.count(),
                Organization.objects.count(),
                Team.objects.count(),
                Repository.objects.count(),
                RepositoryCollaborator.objects.count(),
                TeamRepositoryPermission.objects.count(),
                OrganizationOwnership.objects.count(),
                Alias.objects.count(),
            ),
            counts,
        )
        self.assertEqual(
            {user.username: user.password for user in User.objects.all()},
            hashes,
        )
        self._assert_repository_inquiries(DEFAULT_ORGANIZATION_NAME)
        self.assertTrue(
            Organization.objects.filter(pk=other.pk, name='Unrelated Org').exists()
        )
        self.assertTrue(
            Repository.objects.filter(pk=other_repo.pk, name='foreign-repo').exists()
        )

    def test_custom_organization_name_keeps_the_same_graph(self):
        call_command(
            'seed_example', organization_name='Acme Lab', stdout=StringIO(),
        )
        self.assertFalse(
            Organization.objects.filter(name=DEFAULT_ORGANIZATION_NAME).exists()
        )
        organization, repository, users = self._assert_repository_inquiries('Acme Lab')
        self._assert_owner_scope(organization, repository, users)
        again = StringIO()
        with CaptureQueriesContext(connection) as captured:
            call_command('seed_example', organization_name='Acme Lab', stdout=again)
        self.assertEqual(_write_sql(captured), [])
        self.assertIn('Status: reused', again.getvalue())

    def test_organization_name_uses_the_field_contract(self):
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(CommandError) as blank:
                call_command('seed_example', organization_name='')
        self.assertEqual(captured.captured_queries, [])
        self.assertIn('not valid for Organization.name', str(blank.exception))
        self.assertEqual(Organization.objects.count(), 0)

        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(CommandError) as too_long:
                call_command('seed_example', organization_name='n' * 41)
        self.assertEqual(captured.captured_queries, [])
        self.assertIn('not valid for Organization.name', str(too_long.exception))
        self.assertEqual(get_user_model().objects.count(), 0)

        name = 'N' * 40
        call_command('seed_example', organization_name=name, stdout=StringIO())
        self.assertTrue(Organization.objects.filter(name=name).exists())
        self.assertTrue(Alias.objects.filter(name=name).exists())
        reused = StringIO()
        call_command('seed_example', organization_name=name, stdout=reused)
        self.assertIn('Status: reused', reused.getvalue())

    def test_partial_graph_fails_before_mutation(self):
        organization = create_organization(DEFAULT_ORGANIZATION_NAME)
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(CommandError) as raised:
                call_command('seed_example')
        self.assertEqual(_write_sql(captured), [])
        self.assertIn('Refusing to change existing rows', str(raised.exception))
        self.assertIn('missing, partial, or different graph', str(raised.exception))
        self.assertEqual(Organization.objects.filter(name=DEFAULT_ORGANIZATION_NAME).count(), 1)
        self.assertEqual(get_user_model().objects.count(), 0)
        self.assertEqual(Team.objects.count(), 0)
        self.assertEqual(Alias.objects.filter(name=DEFAULT_ORGANIZATION_NAME).count(), 1)
        self.assertFalse(
            OrganizationOwnership.objects.filter(organization=organization).exists()
        )

    def test_different_graph_fails_before_mutation(self):
        call_command('seed_example', stdout=StringIO())
        User = get_user_model()
        owner = User.objects.get(username=OWNER_USERNAME)
        owner.set_password('changed-dev-only')
        owner.save()
        changed = owner.password
        team = Team.objects.get(
            organization__name=DEFAULT_ORGANIZATION_NAME, name=TEAM_NAME,
        )
        stranger = User.objects.create_user('stranger', password='other')
        team.members.add(stranger)

        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(CommandError) as raised:
                call_command('seed_example')
        self.assertEqual(_write_sql(captured), [])
        message = str(raised.exception)
        self.assertIn('Refusing to change existing rows', message)
        self.assertIn(DEFAULT_ORGANIZATION_NAME, message)
        owner.refresh_from_db()
        self.assertEqual(owner.password, changed)
        self.assertTrue(owner.check_password('changed-dev-only'))
        self.assertIn(stranger, team.members.all())
        self.assertEqual(
            Organization.objects.filter(name=DEFAULT_ORGANIZATION_NAME).count(),
            1,
        )

    def test_existing_user_without_the_organization_is_left_alone(self):
        User = get_user_model()
        user = User.objects.create_user('example-direct', password='unrelated-secret')
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(CommandError) as raised:
                call_command('seed_example', organization_name='Acme Lab')
        self.assertEqual(_write_sql(captured), [])
        self.assertIn('Refusing to modify unrelated rows', str(raised.exception))
        self.assertIn('example-direct', str(raised.exception))
        self.assertFalse(Organization.objects.filter(name='Acme Lab').exists())
        self.assertEqual(User.objects.count(), 1)
        user.refresh_from_db()
        self.assertFalse(
            Organization.objects.filter(personal_user=user).exists()
        )
        self.assertTrue(user.check_password('unrelated-secret'))

    def test_second_organization_name_does_not_retarget_the_first(self):
        call_command('seed_example', stdout=StringIO())
        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(CommandError) as raised:
                call_command('seed_example', organization_name='Acme Lab')
        self.assertEqual(_write_sql(captured), [])
        self.assertIn('Refusing to modify unrelated rows', str(raised.exception))
        self.assertTrue(
            Organization.objects.filter(name=DEFAULT_ORGANIZATION_NAME).exists()
        )
        self.assertFalse(Organization.objects.filter(name='Acme Lab').exists())
        self._assert_repository_inquiries(DEFAULT_ORGANIZATION_NAME)


class SeedExampleAdminEntranceTests(TestCase):
    @override_settings(AUTHENTICATION_BACKENDS=ADMIN_BACKENDS)
    def test_owner_changelist_is_the_seeded_organization(self):
        other = create_organization('Outside Seed')
        Repository.objects.create(organization=other, name='outside-repo')
        Team.objects.create(organization=other, name='outside-team')
        call_command('seed_example', stdout=StringIO())
        User = get_user_model()
        owner = User.objects.get(username=OWNER_USERNAME)
        outsider = User.objects.get(username=OUTSIDER_USERNAME)
        superuser = User.objects.get(username=SUPERUSER_USERNAME)
        direct = User.objects.get(username=DIRECT_USERNAME)
        organization = Organization.objects.get(name=DEFAULT_ORGANIZATION_NAME)
        repository = Repository.objects.get(
            organization=organization, name=REPOSITORY_TITLE,
        )

        self.assertTrue(owner.has_perm('gh_permissions.view_organization'))
        self.assertTrue(owner.has_perm('gh_permissions.change_repository'))
        self.assertTrue(owner.has_perm('gh_permissions.add_team'))
        self.assertFalse(outsider.has_perm('gh_permissions.view_organization'))
        self.assertFalse(direct.has_perm('gh_permissions.add_team'))
        self.assertTrue(owner.has_perm('gh_permissions.read_repository', repository))
        self.assertFalse(
            direct.has_perm('gh_permissions.admin_repository', repository)
        )

        owner_client = Client()
        owner_client.force_login(owner, backend=ADMIN_BACKENDS[1])
        listed = owner_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertEqual(listed.status_code, 200)
        self.assertContains(listed, DEFAULT_ORGANIZATION_NAME)
        self.assertNotContains(listed, 'Outside Seed')
        repos = owner_client.get(reverse('admin:gh_permissions_repository_changelist'))
        self.assertEqual(repos.status_code, 200)
        self.assertContains(repos, REPOSITORY_TITLE)
        self.assertNotContains(repos, 'outside-repo')
        teams = owner_client.get(reverse('admin:gh_permissions_team_changelist'))
        self.assertContains(teams, TEAM_NAME)
        self.assertNotContains(teams, 'outside-team')

        outsider_client = Client()
        outsider_client.force_login(outsider, backend=ADMIN_BACKENDS[1])
        denied = outsider_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertEqual(denied.status_code, 302)
        self.assertIn('/admin/login/', denied['Location'])

        super_client = Client()
        super_client.force_login(superuser, backend=ADMIN_BACKENDS[1])
        everything = super_client.get(
            reverse('admin:gh_permissions_organization_changelist'),
        )
        self.assertEqual(everything.status_code, 200)
        self.assertContains(everything, DEFAULT_ORGANIZATION_NAME)
        self.assertContains(everything, 'Outside Seed')
        manage = _permission('organization', 'manage_organization')
        self.assertNotIn(
            organization,
            set(Organization.objects.authorized(superuser, manage)),
        )
        self.assertIn(
            organization,
            set(Organization.objects.authorized(owner, manage)),
        )


class SeedExamplePlacementTests(SimpleTestCase):
    def test_command_is_example_project_code(self):
        self.assertEqual(get_commands()['seed_example'], 'example')
        command_path = (
            ROOT / 'example' / 'management' / 'commands' / 'seed_example.py'
        )
        self.assertTrue(command_path.is_file())
        source = command_path.read_text()
        self.assertNotIn('OrganizationOwnerPermission', source)
        self.assertNotIn('UserRepositoryPermission', source)
        self.assertIn('create_user', source)
        self.assertIn('create_organization', source)
        self.assertIn('RepositoryCollaborator', source)
        self.assertIn('OrganizationOwnership', source)
        self.assertFalse(list((ROOT / 'gh_permissions').rglob('seed_example.py')))
        library = '\n'.join(
            path.read_text()
            for path in (ROOT / 'gh_permissions').rglob('*.py')
        )
        self.assertNotIn('seed_example', library)
        self.assertNotIn('example-superuser-dev-only', library)
        pyproject = (ROOT / 'pyproject.toml').read_text()
        self.assertIn('include = ["gh_permissions*"]', pyproject)
        help_text = Command().create_parser('manage.py', 'seed_example').format_help()
        self.assertIn('--organization-name', help_text)
        self.assertIn(DEFAULT_ORGANIZATION_NAME, help_text)
        self.assertIn('development-only', help_text)

    def test_readme_names_the_workflow_and_identities(self):
        readme = (ROOT / 'README.md').read_text()
        self.assertNotIn('@', readme.split('Copyright')[0])
        self.assertIn('python manage.py migrate', readme)
        self.assertIn('python manage.py seed_example', readme)
        self.assertIn('--organization-name', readme)
        self.assertIn(DEFAULT_ORGANIZATION_NAME, readme)
        self.assertIn(TEAM_NAME, readme)
        self.assertIn(REPOSITORY_TITLE, readme)
        self.assertIn(EXCLUDED_CODENAME, readme)
        self.assertIn('development-only', readme)
        self.assertIn('not in the `gh_permissions` package', readme)
        self.assertIn('OrganizationOwnership', readme)
        self.assertIn('RepositoryCollaborator', readme)
        self.assertIn('personal organization', readme)
        self.assertIn('gh_permissions.services', readme)
        for spec in USER_SPECS:
            self.assertIn(spec['username'], readme)
            self.assertIn(spec['password'], readme)
            self.assertIn('dev-only', spec['password'])
