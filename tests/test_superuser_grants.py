"""An active superuser has no free pass in gh-permissions.

Core's checked-user override is not registered. Reverse inquiries,
object ``has_perm``, permission enumeration, ``.authorized``, the
relationship services, and the scoped admin follow the same grants as
any other user. Django's own ``User.has_perm`` and stock
``ModelBackend`` stay as Django ships them.
"""

from pathlib import Path

import django.apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.test import RequestFactory, TestCase

from gh_permissions import admin as admin_module
from gh_permissions.admin import OrganizationAdmin, OrganizationOwnershipAdmin
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import (
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
)
from gh_permissions.services import (
    ManagementDenied,
    add_organization_owner,
    create_organization,
    create_repository,
    create_user,
)
from trusts.decorators import (
    _declared_authorization_guards,
    authorization_required,
)


_READ = 'gh_permissions.read_repository'
_MANAGE = 'gh_permissions.manage_organization'


def _catalog():
    return django.apps.apps.get_app_config('gh_permissions').get_catalog()


def _request(user):
    request = HttpRequest()
    request.user = user
    return request


class SuperuserGrantTests(TestCase):
    def setUp(self):
        super().setUp()
        self.organization = create_organization('acme')
        self.repository = Repository.objects.create(
            organization=self.organization, name='app',
        )
        self.read = Permission.objects.get(
            content_type__app_label='gh_permissions',
            content_type__model='repository',
            codename='read_repository',
        )
        self.manage = Permission.objects.get(
            content_type__app_label='gh_permissions',
            content_type__model='organization',
            codename='manage_organization',
        )
        self.owner = create_user('owner')
        self.superuser = create_user(
            'root', is_superuser=True, is_staff=True, is_active=True,
        )
        OrganizationOwnership.objects.create(
            user=self.owner, organization=self.organization,
        )

    def test_no_user_override_is_registered(self):
        self.assertIsNone(_catalog().user_override)
        package = Path(admin_module.__file__).resolve().parent
        for path in package.rglob('*.py'):
            text = path.read_text()
            self.assertNotIn('set_user_override', text, path.name)
            self.assertNotIn('register_user_condition', text, path.name)
        hits = []
        for path in package.rglob('*.py'):
            if 'migrations' in path.parts:
                continue
            for line in path.read_text().splitlines():
                if 'is_superuser' in line:
                    hits.append((path.name, line.strip()))
        self.assertEqual(
            hits,
            [(
                'admin.py',
                "'email', 'is_staff', 'is_active', 'is_superuser',",
            )],
        )

    def test_backend_treats_an_ungranted_superuser_like_any_other_user(self):
        backend = GhAuthorizationBackend()
        stranger = create_user('stranger')
        for user in (self.superuser, stranger):
            self.assertFalse(backend.has_perm(user, _READ, self.repository))
            self.assertFalse(backend.has_perm(user, _MANAGE, self.organization))
            self.assertEqual(
                backend.get_all_permissions(user, self.repository), set(),
            )
            self.assertEqual(
                backend.get_group_permissions(user, self.organization), set(),
            )
            self.assertNotIn(
                self.repository,
                set(Repository.objects.authorized(user, self.read)),
            )
            self.assertNotIn(
                user,
                set(self.repository.get_permitted_users(self.read)),
            )
            self.assertNotIn(
                user,
                set(get_user_model().objects.permitted(
                    self.repository, _READ,
                )),
            )
        self.assertFalse(hasattr(backend, 'with_perm'))
        self.assertEqual(
            backend.get_user_permissions(self.superuser, self.repository),
            set(),
        )
        self.assertEqual(backend.get_user_permissions(stranger), set())
        self.assertFalse(hasattr(Repository.objects, 'with_perm'))
        self.assertFalse(hasattr(Repository, 'with_perm'))
        # Stock PermissionsMixin. Object checks on this backend do not.
        self.assertTrue(self.superuser.has_perm(_READ, self.repository))
        self.assertFalse(stranger.has_perm(_READ, self.repository))
        self.assertNotIn(
            _READ, self.superuser.get_all_permissions(self.repository),
        )

    def test_a_real_grant_includes_the_superuser_and_matches_an_owner(self):
        OrganizationOwnership.objects.create(
            user=self.superuser, organization=self.organization,
        )
        backend = GhAuthorizationBackend()
        for user in (self.owner, self.superuser):
            self.assertTrue(backend.has_perm(user, _MANAGE, self.organization))
            self.assertTrue(backend.has_perm(user, _READ, self.repository))
            self.assertFalse(backend.has_perm(user, _MANAGE, self.repository))
            self.assertIn(
                _MANAGE, backend.get_all_permissions(user, self.organization),
            )
            self.assertIn(
                _READ, backend.get_all_permissions(user, self.repository),
            )
            self.assertEqual(
                backend.get_group_permissions(user, self.repository), set(),
            )
            self.assertIn(
                self.organization,
                set(Organization.objects.authorized(user, self.manage)),
            )
            self.assertIn(
                self.repository,
                set(Repository.objects.authorized(user, self.read)),
            )
            self.assertIn(
                user,
                set(self.repository.get_permitted_users(_READ)),
            )
        permitted = set(get_user_model().objects.permitted(
            self.organization, _READ,
        ))
        self.assertNotIn(self.superuser, permitted)
        self.assertNotIn(self.owner, permitted)

        alone = Repository.objects.create(
            organization=self.organization, name='notes',
        )
        # The ownership row grants read on every repository of that
        # organization. A direct collaborator row is the same inclusion.
        for user in (self.owner, self.superuser):
            self.assertIn(user, set(alone.get_permitted_users(self.read)))
        direct = RepositoryCollaborator.objects.create(
            user=self.superuser, repository=alone,
        )
        direct.permissions.add(self.read)
        self.assertIn(
            self.superuser, set(alone.get_permitted_users(self.read)),
        )

    def test_inactive_superuser_with_a_grant_is_denied(self):
        self.superuser.is_active = False
        self.superuser.save(update_fields=['is_active'])
        OrganizationOwnership.objects.create(
            user=self.superuser, organization=self.organization,
        )
        backend = GhAuthorizationBackend()
        self.assertFalse(
            backend.has_perm(self.superuser, _MANAGE, self.organization),
        )
        self.assertNotIn(
            self.organization,
            set(Organization.objects.authorized(self.superuser, self.manage)),
        )
        self.assertNotIn(
            self.superuser,
            set(get_user_model().objects.permitted(
                self.organization, _MANAGE,
            )),
        )
        with self.assertRaises(ManagementDenied):
            create_repository(
                self.superuser, self.organization.pk, 'idle-repo',
            )
        self.assertFalse(Repository.objects.filter(name='idle-repo').exists())

    def test_services_require_the_same_grant(self):
        bare = create_organization('bare')
        with self.assertRaises(ManagementDenied):
            add_organization_owner(
                self.superuser, bare.pk, self.owner.pk,
            )
        self.assertFalse(bare.ownerships.exists())
        made = create_repository(
            self.owner, self.organization.pk, 'owner-repo',
        )
        self.assertEqual(made.organization_id, self.organization.pk)
        OrganizationOwnership.objects.create(
            user=self.superuser, organization=self.organization,
        )
        made = create_repository(
            self.superuser, self.organization.pk, 'root-repo',
        )
        self.assertEqual(made.organization_id, self.organization.pk)

    def test_admin_scope_does_not_open_for_a_superuser(self):
        request = RequestFactory().get('/admin/')
        request.user = self.superuser
        model_admin = OrganizationAdmin(Organization, admin_module.admin.site)
        self.assertFalse(model_admin.bypasses_scope(request))
        visible = set(model_admin.get_queryset(request))
        self.assertIn(self.superuser.personal_organization, visible)
        self.assertNotIn(self.organization, visible)
        ownership = OrganizationOwnershipAdmin(
            OrganizationOwnership, admin_module.admin.site,
        )
        self.assertFalse(ownership.bypasses_scope(request))
        self.assertFalse(ownership.has_add_permission(request))
        self.assertFalse(ownership.has_change_permission(request))
        self.assertFalse(ownership.has_delete_permission(request))
        with self.assertRaises(PermissionDenied):
            ownership.save_model(request, OrganizationOwnership(), None, False)
        granted = OrganizationOwnership.objects.create(
            user=self.superuser, organization=self.organization,
        )
        self.assertTrue(ownership.has_delete_permission(request, granted))
        with self.assertRaises(PermissionDenied):
            ownership.delete_model(request, granted)
        self.assertTrue(
            OrganizationOwnership.objects.filter(pk=granted.pk).exists(),
        )
        visible = set(model_admin.get_queryset(request))
        self.assertIn(self.organization, visible)
        self.assertIn(self.superuser.personal_organization, visible)

    def test_authorization_required_denies_an_ungranted_superuser(self):
        guarded = authorization_required(Organization, _MANAGE)(
            lambda request, pk: 'ok',
        )
        try:
            with self.assertRaises(PermissionDenied):
                guarded(_request(self.superuser), pk=self.organization.pk)
            self.assertEqual(
                guarded(_request(self.owner), pk=self.organization.pk),
                'ok',
            )
        finally:
            try:
                _declared_authorization_guards.remove(
                    (Organization, _MANAGE, ()),
                )
            except ValueError:
                pass
