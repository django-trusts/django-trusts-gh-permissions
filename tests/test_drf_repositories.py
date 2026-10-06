"""DRF repository list, retrieve, and service-backed create.

Reads use ``Repository.objects.authorized`` before pagination. Retrieve
of a row the caller cannot read is 404, the same body as a missing or
malformed primary key. Create calls ``create_repository``.
"""

import os
import secrets
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.module_loading import import_string
from rest_framework.authentication import SessionAuthentication
from rest_framework.request import Request
from rest_framework.test import APIClient, APIRequestFactory

from example.api import (
    MANAGE_ORGANIZATION,
    READ_REPOSITORY,
    RepositoryActionPermission,
    RepositoryViewSet,
)
from example.authentication import (
    BearerTokenAuthentication,
    _username_for_token,
)
from example.management.commands.seed_example import (
    DEFAULT_ORGANIZATION_NAME,
    DEVELOPMENT_PASSWORDS,
    DIRECT_USERNAME,
    OUTSIDER_USERNAME,
    OWNER_USERNAME,
    REPOSITORY_TITLE,
    SUPERUSER_USERNAME,
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
from gh_permissions.services import (
    delete_repository_collaborator,
    ensure_owner_group,
    replace_team_members_and_ceiling,
)
from trusts.query import is_active_principal


# Steady-state query counts for one already-authenticated JSON request
# on Django 6.1 / SQLite, measured in ``QueryCountTests``. Content types
# are warmed first so the number is the request itself.
LIST_QUERIES = 2
RETRIEVE_QUERIES = 2
DENIED_RETRIEVE_QUERIES = 1
CREATE_QUERIES = 8


def _repository_permission(codename):
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model='repository',
        codename=codename,
    )


class RepositoryApiTests(TestCase):
    """Explicit graph for the authorization cases."""

    def setUp(self):
        TestCase.setUp(self)
        ContentType.objects.get_for_model(Repository)
        ContentType.objects.get_for_model(Organization)
        User = get_user_model()
        group = ensure_owner_group()
        self.read = _repository_permission('read_repository')
        self.write = _repository_permission('write_repository')
        self.org_a = Organization.objects.create(name='acme', owner_group=group)
        self.org_b = Organization.objects.create(name='other', owner_group=group)
        self.owner = User.objects.create(username='owner')
        self.owner_b = User.objects.create(username='owner-b')
        self.reader = User.objects.create(username='reader')
        self.member = User.objects.create(username='member')
        self.both = User.objects.create(username='both')
        self.outsider = User.objects.create(username='outsider')
        self.staff = User.objects.create(username='staff-only', is_staff=True)
        self.superuser = User.objects.create(
            username='root', is_superuser=True, is_active=True,
        )
        self.inactive_superuser = User.objects.create(
            username='inactive-root', is_superuser=True, is_active=False,
        )
        self.inactive_owner = User.objects.create(
            username='inactive-owner', is_active=False,
        )
        self.inactive_reader = User.objects.create(
            username='inactive-reader', is_active=False,
        )
        self.inactive_member = User.objects.create(
            username='inactive-member', is_active=False,
        )
        self.model_user = User.objects.create(username='model-perm')
        self.model_user.user_permissions.add(self.read)
        self.model_user = User.objects.get(pk=self.model_user.pk)
        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.org_a,
        )
        OrganizationOwnership.objects.create(
            user=self.owner_b, organization=self.org_b,
        )
        OrganizationOwnership.objects.create(
            user=self.inactive_owner, organization=self.org_a,
        )
        # Lower primary key than the repositories ``owner`` can read.
        self.repo_b = Repository.objects.create(
            organization=self.org_b, name='secret-repo',
        )
        self.repo_a = Repository.objects.create(
            organization=self.org_a, name='shared-repo',
        )
        self.repo_a2 = Repository.objects.create(
            organization=self.org_a, name='notes-repo',
        )
        self.repo_a3 = Repository.objects.create(
            organization=self.org_a, name='plans-repo',
        )
        self.owned = (self.repo_a, self.repo_a2, self.repo_a3)
        self.reader_row = RepositoryCollaborator.objects.create(
            user=self.reader, repository=self.repo_a,
        )
        self.reader_row.permissions.add(self.read)
        inactive_row = RepositoryCollaborator.objects.create(
            user=self.inactive_reader, repository=self.repo_a,
        )
        inactive_row.permissions.add(self.read)
        both_row = RepositoryCollaborator.objects.create(
            user=self.both, repository=self.repo_a,
        )
        both_row.permissions.add(self.read)
        self.team = Team.objects.create(organization=self.org_a, name='readers')
        self.team.members.add(self.member, self.both, self.inactive_member)
        self.team.allowed_operations.add(self.read)
        TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.repo_a, operation=self.read,
        )
        TeamRepositoryPermission.objects.create(
            team=self.team, repository=self.repo_a, operation=self.write,
        )
        self.client = APIClient()

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def _ids(self, user):
        self._auth(user)
        response = self.client.get('/api/repositories/', {'page_size': 100})
        self.assertEqual(response.status_code, 200, response.content)
        return [row['id'] for row in response.data['results']]

    def _retrieve(self, user, pk):
        self._auth(user)
        return self.client.get('/api/repositories/%s/' % pk)

    def _create(self, user, payload):
        self._auth(user)
        return self.client.post('/api/repositories/', payload, format='json')

    def _assert_reads(self, user, visible):
        """List, retrieve, and ``has_perm`` agree, except active superusers.

        An active superuser's ``has_perm`` is Django's outer true. The
        API still follows ``.authorized()``, so a repository with no
        persisted grant is absent from the list and is 404 on retrieve.
        """
        visible_ids = [repo.pk for repo in visible]
        listed = self._ids(user)
        self.assertEqual(listed, visible_ids)
        outer = bool(user.is_active and user.is_superuser)
        for repo in (self.repo_b,) + self.owned:
            via_perm = user.has_perm(READ_REPOSITORY, repo)
            response = self._retrieve(user, repo.pk)
            if outer:
                self.assertTrue(via_perm)
                if repo.pk in visible_ids:
                    self.assertEqual(response.status_code, 200)
                else:
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.data, {'detail': 'Not found.'})
                continue
            self.assertEqual(via_perm, repo.pk in visible_ids)
            if via_perm:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    set(response.data),
                    {'id', 'name', 'organization_id'},
                )
                self.assertEqual(response.data['name'], repo.name)
            else:
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.data, {'detail': 'Not found.'})
                self.assertNotIn(repo.name, response.content.decode())

    def test_action_map_is_the_only_permission_spelling(self):
        from example.api import RepositoryViewSet
        mapping = RepositoryViewSet.action_permissions
        self.assertEqual(mapping['list'], READ_REPOSITORY)
        self.assertEqual(mapping['retrieve'], READ_REPOSITORY)
        self.assertEqual(mapping['create'], MANAGE_ORGANIZATION)
        self.assertEqual(set(mapping), {'list', 'retrieve', 'create'})

    def test_permitted_string_binds_the_repository_content_type(self):
        organization_type = ContentType.objects.get_for_model(Organization)
        Permission.objects.create(
            name='Same codename on another model',
            content_type=organization_type,
            codename='read_repository',
        )
        listed = list(Repository.objects.permitted(
            READ_REPOSITORY, self.owner,
        ).order_by('pk'))
        self.assertEqual(listed, list(self.owned))
        self._assert_reads(self.owner, self.owned)
        self._assert_reads(self.reader, (self.repo_a,))

    def test_owner_sees_owned_repositories_and_can_create(self):
        self._assert_reads(self.owner, self.owned)
        self.assertTrue(
            self.owner.has_perm(MANAGE_ORGANIZATION, self.org_a),
        )
        before = Repository.objects.count()
        response = self._create(self.owner, {
            'name': 'new-repo',
            'organization_id': self.org_a.pk,
        })
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data['name'], 'new-repo')
        self.assertEqual(response.data['organization_id'], self.org_a.pk)
        self.assertEqual(Repository.objects.count(), before + 1)
        created = Repository.objects.get(pk=response.data['id'])
        self.assertEqual(created.organization_id, self.org_a.pk)
        self.assertIn(created.pk, self._ids(self.owner))
        self.assertNotIn(created.pk, self._ids(self.reader))
        self.assertNotIn(created.pk, self._ids(self.outsider))

    def test_direct_reader_sees_only_the_granted_repository(self):
        self._assert_reads(self.reader, (self.repo_a,))
        self.assertFalse(self.reader.has_perm(READ_REPOSITORY, self.repo_a2))
        self.assertFalse(
            self.reader.has_perm(MANAGE_ORGANIZATION, self.org_a),
        )
        before = set(Repository.objects.values_list('pk', 'name', 'organization_id'))
        response = self._create(self.reader, {
            'name': 'reader-repo',
            'organization_id': self.org_a.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data, {'detail': 'Not allowed.'})
        self.assertEqual(
            set(Repository.objects.values_list('pk', 'name', 'organization_id')),
            before,
        )

    def test_team_member_read_ceiling_excludes_stored_write_grant(self):
        self._assert_reads(self.member, (self.repo_a,))
        self.assertTrue(self.member.has_perm(READ_REPOSITORY, self.repo_a))
        self.assertFalse(self.member.has_perm('gh_permissions.write_repository', self.repo_a))
        self.assertFalse(
            self.repo_a in list(Repository.objects.authorized(self.member, self.write)),
        )
        response = self._create(self.member, {
            'name': 'team-repo',
            'organization_id': self.org_a.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data, {'detail': 'Not allowed.'})
        self.assertFalse(Repository.objects.filter(name='team-repo').exists())

    def test_duplicate_grant_paths_return_one_row(self):
        self.assertTrue(self.both.teams.filter(pk=self.team.pk).exists())
        self.assertTrue(
            RepositoryCollaborator.objects.filter(
                user=self.both, repository=self.repo_a,
            ).exists(),
        )
        listed = self._ids(self.both)
        self.assertEqual(listed, [self.repo_a.pk])
        self.assertEqual(listed.count(self.repo_a.pk), 1)

    def test_outsider_and_staff_see_nothing_and_cannot_create(self):
        self._assert_reads(self.outsider, ())
        self._assert_reads(self.staff, ())
        self.assertTrue(self.staff.is_staff)
        self.assertFalse(self.staff.is_superuser)
        for user in (self.outsider, self.staff, self.owner_b):
            response = self._create(user, {
                'name': 'nope-repo',
                'organization_id': self.org_a.pk,
            })
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data, {'detail': 'Not allowed.'})
        self.assertFalse(Repository.objects.filter(name='nope-repo').exists())
        self._assert_reads(self.owner_b, (self.repo_b,))

    def test_inactive_principals_match_has_perm_not_the_raw_queryset(self):
        # ``authorized()`` still skips inactivity. ``permitted()`` applies
        # it, and the API uses ``permitted()``.
        self.assertFalse(is_active_principal(self.inactive_owner))
        self.assertIn(
            self.repo_a,
            list(Repository.objects.authorized(self.inactive_owner, self.read)),
        )
        self.assertEqual(
            list(Repository.objects.permitted(READ_REPOSITORY, self.inactive_owner)),
            [],
        )
        self.assertIn(
            self.repo_a,
            list(Repository.objects.authorized(self.inactive_reader, self.read)),
        )
        self.assertIn(
            self.repo_a,
            list(Repository.objects.authorized(self.inactive_member, self.read)),
        )
        for user in (
            self.inactive_owner, self.inactive_reader, self.inactive_member,
            self.inactive_superuser,
        ):
            self.assertFalse(user.has_perm(READ_REPOSITORY, self.repo_a))
            self._assert_reads(user, ())
            response = self._create(user, {
                'name': 'inactive-repo',
                'organization_id': self.org_a.pk,
            })
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data, {'detail': 'Not allowed.'})
        self.assertFalse(Repository.objects.filter(name='inactive-repo').exists())

    def test_active_superuser_list_follows_authorized_not_has_perm(self):
        self.assertTrue(is_active_principal(self.superuser))
        self.assertTrue(self.superuser.has_perm(READ_REPOSITORY, self.repo_b))
        self.assertNotIn(
            self.repo_b,
            list(Repository.objects.authorized(self.superuser, self.read)),
        )
        self.assertNotIn(
            self.repo_b,
            list(Repository.objects.permitted(READ_REPOSITORY, self.superuser)),
        )
        self._assert_reads(self.superuser, ())
        response = self._create(self.superuser, {
            'name': 'super-repo',
            'organization_id': self.org_a.pk,
        })
        self.assertEqual(response.status_code, 201, response.content)
        created = Repository.objects.get(name='super-repo')
        self.assertEqual(created.organization_id, self.org_a.pk)
        self.assertNotIn(created.pk, self._ids(self.superuser))
        self.assertTrue(self.superuser.has_perm(READ_REPOSITORY, created))
        denied = self._retrieve(self.superuser, created.pk)
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.data, {'detail': 'Not found.'})
        self.assertIn(created.pk, self._ids(self.owner))

    def test_model_backend_permission_does_not_grant_the_object(self):
        self.assertTrue(self.model_user.has_perm(READ_REPOSITORY))
        self.assertFalse(self.model_user.has_perm(READ_REPOSITORY, self.repo_a))
        self._assert_reads(self.model_user, ())
        response = self._create(self.model_user, {
            'name': 'model-repo',
            'organization_id': self.org_a.pk,
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Repository.objects.filter(name='model-repo').exists())

    def test_anonymous_requests_are_rejected_without_repository_rows(self):
        with CaptureQueriesContext(connection) as captured:
            listed = self.client.get('/api/repositories/')
            fetched = self.client.get('/api/repositories/%s/' % self.repo_a.pk)
            created = self.client.post('/api/repositories/', {
                'name': 'anon-repo',
                'organization_id': self.org_a.pk,
            }, format='json')
        for response in (listed, fetched, created):
            # Bearer authentication is first and sends WWW-Authenticate,
            # so a request with no credentials is DRF's 401.
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response['WWW-Authenticate'], 'Bearer')
            self.assertEqual(
                response.data,
                {'detail': 'Authentication credentials were not provided.'},
            )
            self.assertNotIn(self.repo_a.name, response.content.decode())
            self.assertNotIn(self.repo_b.name, response.content.decode())
        self.assertFalse(Repository.objects.filter(name='anon-repo').exists())
        blob = ' '.join(query['sql'] for query in captured)
        self.assertNotIn('gh_permissions_repository', blob)

    def test_malformed_missing_and_unauthorized_pks_are_the_same_404(self):
        missing = Repository.objects.order_by('pk').last().pk + 1000
        pks = (
            'nope',
            '1.5',
            '0',
            '-1',
            '999999999999999999999999',
            missing,
            self.repo_b.pk,
        )
        for pk in pks:
            response = self._retrieve(self.owner, pk)
            self.assertEqual(response.status_code, 404, pk)
            self.assertEqual(response.data, {'detail': 'Not found.'})
            body = response.content.decode()
            self.assertNotIn(self.repo_b.name, body)
            self.assertNotIn(self.repo_a.name, body)

    def test_pagination_runs_after_authorization(self):
        self.assertLess(self.repo_b.pk, self.repo_a.pk)
        self._auth(self.owner)
        page = self.client.get('/api/repositories/', {'page_size': 2})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.data['count'], 3)
        self.assertEqual(
            [row['id'] for row in page.data['results']],
            [self.repo_a.pk, self.repo_a2.pk],
        )
        self.assertNotIn(self.repo_b.pk, [row['id'] for row in page.data['results']])
        follow = self.client.get(page.data['next'])
        self.assertEqual(follow.status_code, 200)
        self.assertEqual(
            [row['id'] for row in follow.data['results']],
            [self.repo_a3.pk],
        )
        self.assertIsNone(follow.data['next'])

    def test_list_queryset_is_the_authorized_queryset(self):
        from django.test import RequestFactory

        from example.api import RepositoryViewSet

        request = RequestFactory().get('/api/repositories/')
        request.user = self.owner
        view = RepositoryViewSet()
        view.request = request
        view.action = 'list'
        view.format_kwarg = None
        queryset = view.get_queryset()
        sql = str(queryset.query)
        self.assertIn('WHERE', sql)
        self.assertEqual(
            list(queryset.values_list('pk', flat=True)),
            [self.repo_a.pk, self.repo_a2.pk, self.repo_a3.pk],
        )

    def test_revocation_applies_on_the_next_request(self):
        self.assertEqual(self._ids(self.reader), [self.repo_a.pk])
        delete_repository_collaborator(self.owner, self.reader_row.pk)
        self.assertEqual(self._ids(self.reader), [])
        self.assertEqual(self._retrieve(self.reader, self.repo_a.pk).status_code, 404)
        self.assertFalse(self.reader.has_perm(READ_REPOSITORY, self.repo_a))

        self.assertIn(self.repo_a.pk, self._ids(self.member))
        replace_team_members_and_ceiling(
            self.owner, self.team.pk, [], [self.read.pk],
        )
        self.assertEqual(self._ids(self.member), [])
        self.assertEqual(self._retrieve(self.member, self.repo_a.pk).status_code, 404)

    def test_hostile_cross_organization_payloads_fail_closed(self):
        before = set(Repository.objects.values_list('pk', 'name', 'organization_id'))
        swapped = self._create(self.owner, {
            'name': self.repo_b.name,
            'organization_id': self.org_b.pk,
        })
        self.assertEqual(swapped.status_code, 403)
        self.assertEqual(swapped.data, {'detail': 'Not allowed.'})
        self.assertNotIn(self.org_b.name, swapped.content.decode())
        hostile = self._create(self.owner, {
            'name': 'pwned',
            'organization_id': self.org_b.pk,
            'user': self.owner_b.pk,
            'team': self.team.pk,
            'collaborator': self.reader.pk,
            'permissions': [self.read.pk],
            'repository': self.repo_b.pk,
            'id': self.repo_b.pk,
        })
        self.assertEqual(hostile.status_code, 400)
        for key in ('user', 'team', 'collaborator', 'permissions', 'repository', 'id'):
            self.assertIn(key, hostile.data)
        missing = self._create(self.owner, {
            'name': 'missing-org',
            'organization_id': self.org_b.pk + 1000,
        })
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.data, {'detail': 'Not allowed.'})
        malformed = self._create(self.owner, {
            'name': 'bad-org',
            'organization_id': 'nope',
        })
        self.assertEqual(malformed.status_code, 400)
        blank = self._create(self.owner, {
            'name': '   ',
            'organization_id': self.org_a.pk,
        })
        self.assertEqual(blank.status_code, 400)
        spaced = self._create(self.owner, {
            'name': '  spaced',
            'organization_id': self.org_a.pk,
        })
        self.assertEqual(spaced.status_code, 400)
        duplicate = self._create(self.owner, {
            'name': self.repo_a.name,
            'organization_id': self.org_a.pk,
        })
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('name', duplicate.data)
        self.assertEqual(
            set(Repository.objects.values_list('pk', 'name', 'organization_id')),
            before,
        )

    def test_object_permission_disagreement_is_404(self):
        with patch.object(
            RepositoryActionPermission,
            'has_object_permission',
            return_value=False,
        ):
            response = self._retrieve(self.owner, self.repo_a.pk)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, {'detail': 'Not found.'})
        self.assertNotIn(self.repo_a.name, response.content.decode())

    def test_list_query_count_does_not_grow_with_rows(self):
        self._auth(self.owner)
        with CaptureQueriesContext(connection) as small:
            first = self.client.get('/api/repositories/', {'page_size': 100})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(small), LIST_QUERIES, [q['sql'] for q in small])
        for index in range(5):
            Repository.objects.create(
                organization=self.org_a, name='extra-%s' % index,
            )
            Repository.objects.create(
                organization=self.org_b, name='hidden-%s' % index,
            )
        with CaptureQueriesContext(connection) as large:
            second = self.client.get('/api/repositories/', {'page_size': 100})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['count'], 8)
        self.assertEqual(len(large), len(small))
        self.assertEqual(len(large), LIST_QUERIES)

    def test_representative_query_counts(self):
        self._auth(self.owner)
        with CaptureQueriesContext(connection) as listed:
            list_response = self.client.get('/api/repositories/')
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(listed), LIST_QUERIES, [q['sql'] for q in listed])

        with CaptureQueriesContext(connection) as fetched:
            retrieve_response = self._retrieve(self.owner, self.repo_a.pk)
        self.assertEqual(retrieve_response.status_code, 200)
        self.assertEqual(
            len(fetched), RETRIEVE_QUERIES, [q['sql'] for q in fetched],
        )

        with CaptureQueriesContext(connection) as denied:
            denied_response = self._retrieve(self.owner, self.repo_b.pk)
        self.assertEqual(denied_response.status_code, 404)
        self.assertEqual(
            len(denied), DENIED_RETRIEVE_QUERIES, [q['sql'] for q in denied],
        )

        with CaptureQueriesContext(connection) as created:
            create_response = self._create(self.owner, {
                'name': 'counted-repo',
                'organization_id': self.org_a.pk,
            })
        self.assertEqual(create_response.status_code, 201, create_response.content)
        self.assertEqual(
            len(created), CREATE_QUERIES, [q['sql'] for q in created],
        )


def _bearer_client(token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION='Bearer %s' % token)
    return client


class BearerTokenApiTests(TestCase):
    """Bearer results match the session client. Tokens are generated."""

    def setUp(self):
        RepositoryApiTests.setUp(self)

    def test_authentication_order_is_bearer_then_session(self):
        self.assertEqual(
            RepositoryViewSet.authentication_classes,
            (BearerTokenAuthentication, SessionAuthentication),
        )
        configured = tuple(
            import_string(path)
            for path in settings.REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']
        )
        self.assertEqual(configured, RepositoryViewSet.authentication_classes)

    def test_request_auth_does_not_carry_the_bearer_secret(self):
        token = secrets.token_urlsafe(32)
        raw = APIRequestFactory().get(
            '/api/repositories/',
            HTTP_AUTHORIZATION='Bearer %s' % token,
        )
        with override_settings(EXAMPLE_API_TOKENS={token: self.owner.username}):
            user, marker = BearerTokenAuthentication().authenticate(raw)
            request = Request(raw, authenticators=(BearerTokenAuthentication(),))
            self.assertEqual(request.user.pk, self.owner.pk)
            self.assertIsNone(request.auth)
            self.assertIsNone(marker)
            self.assertEqual(user.pk, self.owner.pk)
        self.assertNotIn(token, '' if request.auth is None else str(request.auth))
        self.assertNotEqual(request.auth, token)

    def test_digest_compare_checks_every_configured_token(self):
        shorter = secrets.token_hex(16)
        longer = shorter + secrets.token_hex(4)
        with override_settings(EXAMPLE_API_TOKENS={
            shorter: self.owner.username,
            longer: self.outsider.username,
        }):
            self.assertEqual(_username_for_token(shorter), self.owner.username)
            self.assertEqual(_username_for_token(longer), self.outsider.username)
            self.assertIsNone(_username_for_token(shorter[:-2]))
            self.assertIsNone(_username_for_token(secrets.token_hex(16)))
        with override_settings(EXAMPLE_API_TOKENS=['not-a-mapping']):
            self.assertIsNone(_username_for_token(shorter))

    def test_bearer_list_retrieve_and_create_match_session_results(self):
        owner_token = secrets.token_hex(32)
        outsider_token = secrets.token_hex(32)
        reader_token = secrets.token_hex(32)
        payload = {
            'name': 'bearer-repo',
            'organization_id': self.org_a.pk,
        }
        session_owner = APIClient()
        session_owner.force_authenticate(user=self.owner)
        session_outsider = APIClient()
        session_outsider.force_authenticate(user=self.outsider)
        session_reader = APIClient()
        session_reader.force_authenticate(user=self.reader)
        session_list = session_owner.get('/api/repositories/')
        session_retrieve = session_outsider.get(
            '/api/repositories/%s/' % self.repo_a.pk,
        )
        session_create = session_reader.post(
            '/api/repositories/', payload, format='json',
        )

        with override_settings(EXAMPLE_API_TOKENS={
            owner_token: self.owner.username,
            outsider_token: self.outsider.username,
            reader_token: self.reader.username,
        }):
            with CaptureQueriesContext(connection) as listed:
                bearer_list = _bearer_client(owner_token).get('/api/repositories/')
            # The next requests clear the query log. Read this count first.
            # The user lookup is the only query beyond the force-authenticated list.
            bearer_list_queries = len(listed)
            bearer_list_sql = [query['sql'] for query in listed]
            bearer_retrieve = _bearer_client(outsider_token).get(
                '/api/repositories/%s/' % self.repo_a.pk,
            )
            bearer_create = _bearer_client(reader_token).post(
                '/api/repositories/', payload, format='json',
            )

        self.assertEqual(session_list.status_code, 200)
        self.assertEqual(bearer_list.status_code, 200)
        self.assertEqual(bearer_list.data, session_list.data)
        self.assertIn(self.repo_a.pk, [row['id'] for row in bearer_list.data['results']])
        self.assertNotIn(
            self.repo_b.pk, [row['id'] for row in bearer_list.data['results']],
        )
        self.assertEqual(bearer_list_queries, LIST_QUERIES + 1, bearer_list_sql)

        self.assertEqual(session_retrieve.status_code, 404)
        self.assertEqual(bearer_retrieve.status_code, 404)
        self.assertEqual(bearer_retrieve.data, {'detail': 'Not found.'})
        self.assertEqual(bearer_retrieve.data, session_retrieve.data)
        self.assertNotIn(self.repo_a.name, bearer_retrieve.content.decode())

        self.assertEqual(session_create.status_code, 403)
        self.assertEqual(bearer_create.status_code, 403)
        self.assertEqual(bearer_create.data, {'detail': 'Not allowed.'})
        self.assertEqual(bearer_create.data, session_create.data)
        self.assertFalse(Repository.objects.filter(name='bearer-repo').exists())

    def test_missing_and_invalid_bearer_tokens_are_401(self):
        configured = secrets.token_hex(32)
        unknown = secrets.token_hex(32)
        with override_settings(EXAMPLE_API_TOKENS={configured: self.owner.username}):
            missing = self.client.get('/api/repositories/')
            unknown_response = _bearer_client(unknown).get('/api/repositories/')
            keyword_only = APIClient()
            keyword_only.credentials(HTTP_AUTHORIZATION='Bearer')
            malformed = keyword_only.get('/api/repositories/')
            other_scheme = APIClient()
            other_scheme.credentials(HTTP_AUTHORIZATION='Token %s' % configured)
            ignored = other_scheme.get('/api/repositories/')
        for response in (missing, ignored):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response['WWW-Authenticate'], 'Bearer')
            self.assertEqual(
                response.data,
                {'detail': 'Authentication credentials were not provided.'},
            )
        self.assertEqual(unknown_response.status_code, 401)
        self.assertEqual(unknown_response['WWW-Authenticate'], 'Bearer')
        self.assertEqual(unknown_response.data, {'detail': 'Invalid token.'})
        self.assertEqual(malformed.status_code, 401)
        self.assertEqual(
            malformed.data,
            {'detail': 'Invalid token header. No credentials provided.'},
        )

    def test_inactive_bearer_user_is_denied(self):
        token = secrets.token_hex(32)
        with override_settings(EXAMPLE_API_TOKENS={
            token: self.inactive_owner.username,
        }):
            with CaptureQueriesContext(connection) as captured:
                response = _bearer_client(token).get('/api/repositories/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response['WWW-Authenticate'], 'Bearer')
        self.assertEqual(response.data, {'detail': 'User inactive or deleted.'})
        self.assertNotIn(self.repo_a.name, response.content.decode())
        blob = ' '.join(query['sql'] for query in captured)
        self.assertNotIn('gh_permissions_repository', blob)

    def test_is_active_principal_rejects_a_mapped_user(self):
        token = secrets.token_hex(32)
        with override_settings(EXAMPLE_API_TOKENS={token: self.owner.username}):
            with patch(
                'example.authentication.is_active_principal',
                return_value=False,
            ):
                response = _bearer_client(token).get('/api/repositories/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data, {'detail': 'User inactive or deleted.'})

    def test_session_login_still_works_without_a_bearer_header(self):
        self.owner.set_password('session-password')
        self.owner.save(update_fields=['password'])
        self.assertTrue(self.client.login(
            username=self.owner.username,
            password='session-password',
        ))
        response = self.client.get('/api/repositories/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.repo_a.pk, [row['id'] for row in response.data['results']])

        unknown = secrets.token_hex(32)
        self.client.credentials(HTTP_AUTHORIZATION='Bearer %s' % unknown)
        rejected = self.client.get('/api/repositories/')
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.data, {'detail': 'Invalid token.'})

    def test_unknown_username_in_the_mapping_is_an_authentication_failure(self):
        token = secrets.token_hex(32)
        with override_settings(EXAMPLE_API_TOKENS={token: 'missing-user'}):
            response = _bearer_client(token).get('/api/repositories/')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data, {'detail': 'Invalid token.'})


class BearerTokenSettingsTests(SimpleTestCase):
    """Environment variables populate the mapping. Tokens are generated."""

    def test_seeded_usernames_are_the_mapping_targets(self):
        from tests.settings import _EXAMPLE_API_TOKEN_USERS

        self.assertEqual(
            _EXAMPLE_API_TOKEN_USERS,
            (
                ('EXAMPLE_API_TOKEN_OWNER', OWNER_USERNAME),
                ('EXAMPLE_API_TOKEN_DIRECT', DIRECT_USERNAME),
                ('EXAMPLE_API_TOKEN_OUTSIDER', OUTSIDER_USERNAME),
            ),
        )

    def test_unset_and_empty_variables_add_no_entry(self):
        from tests.settings import _example_api_tokens

        owner = secrets.token_hex(16)
        direct = secrets.token_hex(16)
        outsider = secrets.token_hex(16)
        with patch.dict(os.environ, {
            'EXAMPLE_API_TOKEN_OWNER': owner,
            'EXAMPLE_API_TOKEN_DIRECT': direct,
            'EXAMPLE_API_TOKEN_OUTSIDER': outsider,
        }):
            os.environ.pop('EXAMPLE_API_TOKEN_OWNER')
            os.environ.pop('EXAMPLE_API_TOKEN_DIRECT')
            os.environ.pop('EXAMPLE_API_TOKEN_OUTSIDER')
            self.assertEqual(_example_api_tokens(), {})
        with patch.dict(os.environ, {
            'EXAMPLE_API_TOKEN_OWNER': '',
            'EXAMPLE_API_TOKEN_DIRECT': '',
            'EXAMPLE_API_TOKEN_OUTSIDER': '',
        }):
            self.assertEqual(_example_api_tokens(), {})

    def test_environment_populates_the_three_seeded_users(self):
        from tests.settings import _example_api_tokens

        owner = secrets.token_urlsafe(32)
        direct = secrets.token_urlsafe(32)
        outsider = secrets.token_urlsafe(32)
        with patch.dict(os.environ, {
            'EXAMPLE_API_TOKEN_OWNER': owner,
            'EXAMPLE_API_TOKEN_DIRECT': direct,
            'EXAMPLE_API_TOKEN_OUTSIDER': outsider,
        }):
            self.assertEqual(_example_api_tokens(), {
                owner: OWNER_USERNAME,
                direct: DIRECT_USERNAME,
                outsider: OUTSIDER_USERNAME,
            })

    def test_one_token_in_two_variables_is_a_configuration_error(self):
        from tests.settings import _example_api_tokens

        shared = secrets.token_urlsafe(32)
        with patch.dict(os.environ, {
            'EXAMPLE_API_TOKEN_OWNER': shared,
            'EXAMPLE_API_TOKEN_DIRECT': shared,
            'EXAMPLE_API_TOKEN_OUTSIDER': '',
        }):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                _example_api_tokens()
        self.assertNotIn(shared, str(ctx.exception))

    def test_minimum_length_matches_token_urlsafe_32(self):
        from tests.settings import EXAMPLE_API_TOKEN_MIN_LENGTH

        self.assertEqual(
            EXAMPLE_API_TOKEN_MIN_LENGTH,
            len(secrets.token_urlsafe(32)),
        )

    def test_short_token_is_a_configuration_error(self):
        from tests.settings import EXAMPLE_API_TOKEN_MIN_LENGTH, _example_api_tokens

        short = 'x' * (EXAMPLE_API_TOKEN_MIN_LENGTH - 1)
        one = 'y'
        for value in (one, short):
            with patch.dict(os.environ, {
                'EXAMPLE_API_TOKEN_OWNER': value,
                'EXAMPLE_API_TOKEN_DIRECT': '',
                'EXAMPLE_API_TOKEN_OUTSIDER': '',
            }):
                with self.assertRaises(ImproperlyConfigured) as ctx:
                    _example_api_tokens()
            self.assertNotIn(value, str(ctx.exception))
            self.assertIn('EXAMPLE_API_TOKEN_OWNER', str(ctx.exception))
            self.assertIn('secrets.token_urlsafe(32)', str(ctx.exception))


class SeededRepositoryApiTests(TestCase):
    """The development seed, through the same API."""

    def setUp(self):
        super(SeededRepositoryApiTests, self).setUp()
        call_command('seed_example', stdout=StringIO())
        self.client = APIClient()
        User = get_user_model()
        self.users = {
            username: User.objects.get(username=username)
            for username in DEVELOPMENT_PASSWORDS
        }
        self.organization = Organization.objects.get(name=DEFAULT_ORGANIZATION_NAME)
        self.repository = Repository.objects.get(
            organization=self.organization, name=REPOSITORY_TITLE,
        )

    def _ids(self, username):
        self.client.force_authenticate(user=self.users[username])
        response = self.client.get('/api/repositories/', {'page_size': 100})
        self.assertEqual(response.status_code, 200, response.content)
        return [row['name'] for row in response.data['results']]

    def test_seeded_reads_and_organization_create(self):
        self.assertIn(REPOSITORY_TITLE, self._ids(OWNER_USERNAME))
        self.assertIn(REPOSITORY_TITLE, self._ids(DIRECT_USERNAME))
        self.assertIn(REPOSITORY_TITLE, self._ids(TEAM_USERNAME))
        self.assertNotIn(REPOSITORY_TITLE, self._ids(OUTSIDER_USERNAME))
        self.assertNotIn(REPOSITORY_TITLE, self._ids(SUPERUSER_USERNAME))

        team_user = self.users[TEAM_USERNAME]
        self.assertFalse(
            team_user.has_perm('gh_permissions.write_repository', self.repository),
        )
        direct = self.users[DIRECT_USERNAME]
        self.assertFalse(
            direct.has_perm(MANAGE_ORGANIZATION, self.organization),
        )
        self.client.force_authenticate(user=direct)
        denied = self.client.post('/api/repositories/', {
            'name': 'direct-create',
            'organization_id': self.organization.pk,
        }, format='json')
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(Repository.objects.filter(name='direct-create').exists())

        outsider = self.users[OUTSIDER_USERNAME]
        self.client.force_authenticate(user=outsider)
        hidden = self.client.get('/api/repositories/%s/' % self.repository.pk)
        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(hidden.data, {'detail': 'Not found.'})
        self.assertNotIn(REPOSITORY_TITLE, hidden.content.decode())

        superuser = self.users[SUPERUSER_USERNAME]
        self.assertTrue(superuser.has_perm(READ_REPOSITORY, self.repository))
        self.assertNotIn(
            self.repository,
            list(Repository.objects.authorized(superuser, _repository_permission('read_repository'))),
        )
        self.client.force_authenticate(user=superuser)
        super_get = self.client.get('/api/repositories/%s/' % self.repository.pk)
        self.assertEqual(super_get.status_code, 404)

        owner = self.users[OWNER_USERNAME]
        self.client.force_authenticate(user=owner)
        created = self.client.post('/api/repositories/', {
            'name': 'owner-create',
            'organization_id': self.organization.pk,
        }, format='json')
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.data['organization_id'], self.organization.pk)
        self.assertIn('owner-create', self._ids(OWNER_USERNAME))
        self.assertNotIn('owner-create', self._ids(DIRECT_USERNAME))
        self.assertNotIn('owner-create', self._ids(SUPERUSER_USERNAME))

    def test_non_staff_seed_users_call_the_api_with_bearer_tokens(self):
        """Admin login cannot start these sessions. Bearer can.

        ``example-direct`` and ``example-outsider`` are not staff.
        ``EXAMPLE_API_TOKEN_DIRECT`` and ``EXAMPLE_API_TOKEN_OUTSIDER``
        are the live curl path for those seeded users.
        """
        direct = self.users[DIRECT_USERNAME]
        outsider = self.users[OUTSIDER_USERNAME]
        self.assertFalse(direct.is_staff)
        self.assertFalse(outsider.is_staff)
        self.assertTrue(self.users[OWNER_USERNAME].is_staff)
        owner_token = secrets.token_hex(32)
        direct_token = secrets.token_hex(32)
        outsider_token = secrets.token_hex(32)
        with override_settings(EXAMPLE_API_TOKENS={
            owner_token: OWNER_USERNAME,
            direct_token: DIRECT_USERNAME,
            outsider_token: OUTSIDER_USERNAME,
        }):
            owner_list = _bearer_client(owner_token).get('/api/repositories/')
            direct_list = _bearer_client(direct_token).get('/api/repositories/')
            direct_create = _bearer_client(direct_token).post('/api/repositories/', {
                'name': 'direct-bearer-repo',
                'organization_id': self.organization.pk,
            }, format='json')
            outsider_retrieve = _bearer_client(outsider_token).get(
                '/api/repositories/%s/' % self.repository.pk,
            )
        self.assertEqual(owner_list.status_code, 200)
        self.assertIn(REPOSITORY_TITLE, [row['name'] for row in owner_list.data['results']])
        self.assertEqual(direct_list.status_code, 200)
        self.assertIn(
            REPOSITORY_TITLE, [row['name'] for row in direct_list.data['results']],
        )
        self.assertEqual(direct_create.status_code, 403)
        self.assertEqual(direct_create.data, {'detail': 'Not allowed.'})
        self.assertFalse(
            Repository.objects.filter(name='direct-bearer-repo').exists(),
        )
        self.assertEqual(outsider_retrieve.status_code, 404)
        self.assertEqual(outsider_retrieve.data, {'detail': 'Not found.'})
        self.assertNotIn(REPOSITORY_TITLE, outsider_retrieve.content.decode())


class ManualProofScriptTests(TestCase):
    """The script is the manual allowed/denied proof."""

    def test_script_prints_one_allowed_and_one_denied_request(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'drf_authorization_proof.py'
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(script.parent.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        self.assertIn(
            'ALLOWED example-owner GET /api/repositories/ 200',
            completed.stdout,
        )
        self.assertIn('DENIED example-outsider GET /api/repositories/', completed.stdout)
        self.assertIn(' 404\n', completed.stdout)
        self.assertIn(
            'DENIED example-direct POST /api/repositories/ 403',
            completed.stdout,
        )
        retrieve = completed.stdout.split('DENIED example-outsider', 1)[1]
        retrieve = retrieve.split('DENIED example-direct', 1)[0]
        self.assertIn('Not found.', retrieve)
        self.assertNotIn('shared-repo', retrieve)
