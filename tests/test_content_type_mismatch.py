"""Owner registration denies a permission used on the wrong content type.

Core django-trusts#267 / #269. The shared owner group still holds
``manage_organization`` and the repository codenames. Each permission
grants only when ``Permission.content_type`` is the protected object's
own content identity. The crossed pairs are denials, not exceptions.
``Team`` and ``Alias`` have no content-terminal registration, so they
stay the ordinary no-plan denial.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.test import TestCase
import yaml

from trusts.decorators import authorization_required
from trusts.models import AuthorizedQuerySet

from gh_permissions.backends import GhAuthorizationBackend
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
from tests.fixtures import (
    guard_ok,
    permission_code,
    policy_sql_text,
    repository_permission,
)


_MANAGE = 'gh_permissions.manage_organization'
_READ = 'gh_permissions.read_repository'
_REPO_CODES = {
    'gh_permissions.read_repository',
    'gh_permissions.write_repository',
    'gh_permissions.admin_repository',
}


_org_manage = authorization_required(Organization, _MANAGE)(guard_ok)
_org_read = authorization_required(Organization, _READ)(guard_ok)
_repo_read = authorization_required(Repository, _READ)(guard_ok)
_repo_manage = authorization_required(Repository, _MANAGE)(guard_ok)


def _permission(model, codename):
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model=model,
        codename=codename,
    )


def _request(user):
    request = HttpRequest()
    request.user = user
    request.META['SERVER_NAME'] = 'testserver'
    request.META['SERVER_PORT'] = '80'
    return request


class OwnerContentTypeMatrixTest(TestCase):
    def setUp(self):
        super().setUp()
        self.owner = create_user('owner')
        self.bystander = create_user('bystander')
        self.organization = create_organization('acme')
        self.foreign = create_organization('foreign')
        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.organization,
        )
        self.repository = Repository.objects.create(
            organization=self.organization, name='app',
        )
        self.team = Team.objects.create(
            organization=self.organization, name='writers',
        )
        self.alias = Alias.objects.get(name='acme')
        self.manage = _permission('organization', 'manage_organization')
        self.read = repository_permission('read_repository')
        self.backend = GhAuthorizationBackend()

    def test_same_model_grants_and_crossed_pairs_deny(self):
        cases = (
            (_MANAGE, self.manage, self.organization, True, 1),
            (_READ, self.read, self.repository, True, 1),
            (_READ, self.read, self.organization, False, 1),
            (_MANAGE, self.manage, self.repository, False, 1),
            (_MANAGE, self.manage, self.team, False, 0),
            (_READ, self.read, self.alias, False, 0),
        )
        for code, permission, obj, expected, queries in cases:
            with self.subTest(code=code, model=obj._meta.model_name):
                with self.assertNumQueries(queries):
                    self.assertIs(self.owner.has_perm(code, obj), expected)
                self.assertIs(
                    self.backend.has_perm(self.owner, permission_code(permission), obj),
                    expected,
                )
                self.assertIs(self.bystander.has_perm(code, obj), False)

    def test_enumeration_drops_the_other_models_codenames(self):
        self.assertEqual(
            self.owner.get_all_permissions(self.organization),
            {_MANAGE},
        )
        self.assertEqual(
            self.owner.get_all_permissions(self.repository),
            _REPO_CODES,
        )
        self.assertNotIn(
            _READ, self.owner.get_all_permissions(self.organization),
        )
        self.assertNotIn(
            _MANAGE, self.owner.get_all_permissions(self.repository),
        )
        self.assertEqual(self.owner.get_all_permissions(self.team), set())
        self.assertEqual(self.owner.get_all_permissions(self.alias), set())
        self.assertEqual(
            self.owner.get_group_permissions(self.organization), set(),
        )
        self.assertEqual(
            self.owner.get_group_permissions(self.repository), set(),
        )
        self.assertEqual(
            self.bystander.get_all_permissions(self.organization), set(),
        )
        self.assertEqual(
            self.bystander.get_all_permissions(self.repository), set(),
        )

    def test_authorized_and_reverse_agree_with_has_perm(self):
        User = get_user_model()
        self.assertIn(
            self.organization,
            set(Organization.objects.authorized(self.owner, self.manage)),
        )
        self.assertEqual(
            list(Organization.objects.authorized(self.owner, self.read)),
            [],
        )
        self.assertEqual(
            list(Repository.objects.authorized(self.owner, self.read)),
            [self.repository],
        )
        self.assertEqual(
            list(Repository.objects.authorized(self.owner, self.manage)),
            [],
        )
        self.assertEqual(
            list(AuthorizedQuerySet.authorized(
                Team.objects.all(), self.owner, self.manage,
            )),
            [],
        )
        self.assertEqual(
            list(AuthorizedQuerySet.authorized(
                Alias.objects.all(), self.owner, self.read,
            )),
            [],
        )
        self.assertNotIn(
            self.foreign,
            set(Organization.objects.authorized(self.owner, self.manage)),
        )
        self.assertFalse(
            self.owner.has_perm(_MANAGE, self.foreign),
        )

        def usernames(queryset):
            return list(queryset.values_list('username', flat=True))

        self.assertEqual(
            usernames(User.objects.permitted(self.organization, _MANAGE)),
            ['owner'],
        )
        self.assertEqual(
            usernames(User.objects.permitted(self.organization, self.manage)),
            ['owner'],
        )
        self.assertEqual(
            usernames(User.objects.permitted(self.organization, _READ)),
            [],
        )
        self.assertEqual(
            usernames(User.objects.permitted(self.organization, self.read)),
            [],
        )
        self.assertEqual(
            usernames(User.objects.permitted(self.repository, _READ)),
            ['owner'],
        )
        self.assertEqual(
            usernames(self.repository.get_permitted_users(_READ)),
            ['owner'],
        )
        self.assertEqual(
            usernames(self.repository.get_permitted_users(self.read)),
            ['owner'],
        )
        self.assertEqual(
            usernames(User.objects.permitted(self.repository, _MANAGE)),
            [],
        )
        self.assertEqual(
            usernames(self.repository.get_permitted_users(self.manage)),
            [],
        )
        self.assertEqual(
            usernames(User.objects.permitted(self.team, _MANAGE)),
            [],
        )
        self.assertEqual(
            usernames(User.objects.permitted(self.alias, _READ)),
            [],
        )
        self.assertFalse(hasattr(self.organization, 'get_permitted_users'))

    def test_authorization_required_denies_the_crossed_pair(self):
        self.assertEqual(
            _org_manage(_request(self.owner), pk=self.organization.pk), 'ok',
        )
        with self.assertRaises(PermissionDenied):
            _org_read(_request(self.owner), pk=self.organization.pk)
        self.assertEqual(
            _repo_read(_request(self.owner), pk=self.repository.pk),
            'ok',
        )
        with self.assertRaises(PermissionDenied):
            _repo_manage(_request(self.owner), pk=self.repository.pk)

    def test_active_superuser_has_perm_stays_outside_the_predicate(self):
        root = get_user_model().objects.create_superuser(
            'root', password='x', email='root@example.com',
        )
        User = get_user_model()
        with self.assertNumQueries(0):
            self.assertTrue(root.has_perm(_READ, self.organization))
        self.assertNotIn(_READ, root.get_all_permissions(self.organization))
        self.assertEqual(
            list(Organization.objects.authorized(root, self.read)),
            [],
        )
        # No superuser override is registered, so the reverse inquiry
        # does not add an active superuser. The crossed pair stays empty.
        permitted = set(User.objects.permitted(self.organization, _READ))
        self.assertNotIn(root, permitted)
        self.assertNotIn(self.owner, permitted)

    def test_policy_sql_requires_the_protected_models_content_type(self):
        document = yaml.safe_load(policy_sql_text())
        contents = document['backends'][0]['contents']
        seen = set()
        for content in contents:
            model_label = content['model']
            model_name = model_label.split('.')[-1].lower()
            seen.add(model_label)
            for key in (
                'permitted', 'has_perm', 'get_all_permissions',
                'get_permitted_users',
            ):
                statement = content[key]
                sql = statement['sql']
                params = statement['params']
                with self.subTest(model=model_label, key=key):
                    self.assertIn('django_content_type', sql)
                    self.assertIn('$content.app_label="gh_permissions"', params)
                    self.assertIn('$content.model="%s"' % model_name, params)
        self.assertIn('gh_permissions.Organization', seen)
        self.assertIn('gh_permissions.Repository', seen)


class StoredManagePermissionOnRepositoryTest(TestCase):
    """A repository grant that stores an organization permission does not grant it."""

    def test_collaborator_and_team_rows_deny_manage_organization_on_repository(self):
        owner = create_user('owner')
        collaborator = create_user('collaborator')
        member = create_user('member')
        organization = create_organization('acme')
        OrganizationOwnership.objects.create(
            user=owner, organization=organization,
        )
        repository = Repository.objects.create(
            organization=organization, name='app',
        )
        manage = _permission('organization', 'manage_organization')
        collaboration = RepositoryCollaborator.objects.create(
            user=collaborator, repository=repository,
        )
        collaboration.permissions.add(manage)
        team = Team.objects.create(organization=organization, name='writers')
        team.members.add(member)
        team.allowed_operations.add(manage)
        TeamRepositoryPermission.objects.create(
            team=team, repository=repository, operation=manage,
        )
        self.assertFalse(collaborator.has_perm(_MANAGE, repository))
        self.assertFalse(member.has_perm(_MANAGE, repository))
        self.assertEqual(
            list(Repository.objects.authorized(collaborator, manage)),
            [],
        )
        self.assertEqual(
            list(Repository.objects.authorized(member, manage)),
            [],
        )
        self.assertNotIn(_MANAGE, collaborator.get_all_permissions(repository))
        self.assertNotIn(_MANAGE, member.get_all_permissions(repository))
