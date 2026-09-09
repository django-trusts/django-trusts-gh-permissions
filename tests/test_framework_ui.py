"""Framework admin, CBV, templates, backend, and decorator with GH config."""

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse

from trusts.backends import ObjectAuthorizationBackend
from trusts.runtime import filter_authorized, is_authorized

from gh_permissions.admin import RepositoryAdmin
from gh_permissions.models import Repository
from gh_permissions.views import RepositoryDetailView, RepositoryListView
from tests.fixtures import GhFixtureMixin


class GhAdminTest(GhFixtureMixin, TestCase):
    def setUp(self):
        super(GhAdminTest, self).setUp()
        self.factory = RequestFactory()
        self.model_admin = RepositoryAdmin(Repository, AdminSite(name='gh'))

    def _request(self, user, path='/admin/gh_permissions/repository/'):
        request = self.factory.get(path)
        request.user = user
        return request

    def test_queryset_filters_in_sql_before_pagination(self):
        request = self._request(self.member)
        with self.assertNumQueries(1):
            pks = list(
                self.model_admin.get_queryset(request).values_list(
                    'pk', flat=True,
                )
            )
        self.assertEqual(pks, [self.repo_a.pk])
        expected = list(
            filter_authorized(
                Repository.objects.all(), self.member, 'read',
            ).values_list('pk', flat=True)
        )
        self.assertEqual(pks, expected)

    def test_changelist_paginates_authorized_set(self):
        self.model_admin.list_per_page = 1
        request = self._request(self.member)
        changelist = self.model_admin.get_changelist_instance(request)
        self.assertEqual(changelist.result_count, 1)
        self.assertEqual(
            [row.pk for row in changelist.result_list], [self.repo_a.pk],
        )
        self.assertEqual(Repository.objects.count(), 3)

    def test_object_and_list_gates_agree(self):
        request = self._request(self.member)
        authorized = set(
            self.model_admin.get_queryset(request).values_list('pk', flat=True)
        )
        for repo in (self.repo_a, self.repo_b, self.repo_other):
            viewed = self.model_admin.has_view_permission(request, repo)
            self.assertEqual(repo.pk in authorized, viewed)
            self.assertEqual(viewed, is_authorized(self.member, 'read', repo))

    def test_add_is_fail_closed(self):
        request = self._request(self.member)
        self.assertFalse(self.model_admin.has_add_permission(request))

    def test_anonymous_list_is_empty_without_auth_sql(self):
        request = self._request(AnonymousUser())
        with self.assertNumQueries(0):
            self.assertEqual(list(self.model_admin.get_queryset(request)), [])


class GhCBVTest(GhFixtureMixin, TestCase):
    def setUp(self):
        super(GhCBVTest, self).setUp()
        self.factory = RequestFactory()

    def _get(self, user, path='/'):
        request = self.factory.get(path)
        request.user = user
        return request

    def test_list_filters_before_pagination_and_uses_stub_template(self):
        view = RepositoryListView.as_view(paginate_by=1)
        request = self._get(self.member, '/repositories/')
        with self.assertNumQueries(2):
            response = view(request)
            objects = list(response.context_data['object_list'])
        self.assertEqual([obj.pk for obj in objects], [self.repo_a.pk])
        self.assertEqual(response.context_data['paginator'].count, 1)
        rendered = response.render()
        self.assertContains(rendered, 'repo-a')
        self.assertNotContains(rendered, 'repo-b')
        self.assertEqual(
            RepositoryListView.template_name, 'trusts/authorized_list.html',
        )

    def test_detail_granted_and_unauthorized(self):
        view = RepositoryDetailView.as_view()
        request = self._get(self.member)
        with self.assertNumQueries(2):
            response = view(request, pk=self.repo_a.pk)
        self.assertEqual(response.context_data['object'].pk, self.repo_a.pk)
        self.assertContains(response.render(), 'repo-a')
        self.assertEqual(
            RepositoryDetailView.template_name, 'trusts/authorized_detail.html',
        )
        with self.assertNumQueries(2):
            with self.assertRaises(PermissionDenied):
                view(request, pk=self.repo_b.pk)
        missing = self.repo_a.pk + self.repo_b.pk + self.repo_other.pk + 1000
        with self.assertNumQueries(1):
            with self.assertRaises(Http404):
                view(request, pk=missing)


class GhBackendTest(GhFixtureMixin, TestCase):
    def test_object_backend_matches_is_authorized(self):
        backend = ObjectAuthorizationBackend()
        self.assertIsNone(backend.authenticate(None))
        self.assertFalse(backend.has_perm(self.member, 'read'))
        self.assertEqual(
            backend.has_perm(self.member, 'read', self.repo_a),
            is_authorized(self.member, 'read', self.repo_a),
        )
        self.assertFalse(backend.has_perm(self.member, 'read', self.repo_b))
        self.assertTrue(
            backend.has_perm(self.collaborator, 'write', self.repo_b),
        )
        self.assertFalse(
            backend.has_perm(AnonymousUser(), 'read', self.repo_a),
        )


class GhDecoratorTest(GhFixtureMixin, TestCase):
    def test_function_view_uses_require_authorized(self):
        granted = self.client.get(
            reverse('gh-repository-read', kwargs={'pk': self.repo_a.pk}),
        )
        # request.user is Django AnonymousUser; Account is the requester.
        self.assertEqual(granted.status_code, 403)
        from django.test import RequestFactory
        from gh_permissions.views import repository_read

        factory = RequestFactory()
        request = factory.get('/repositories/%s/read/' % self.repo_a.pk)
        request.user = self.member
        response = repository_read(request, pk=self.repo_a.pk)
        self.assertEqual(response.status_code, 200)
        request.user = self.stranger
        with self.assertRaises(PermissionDenied):
            repository_read(request, pk=self.repo_a.pk)
