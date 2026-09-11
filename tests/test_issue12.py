"""#12: bounded consumer-model cleanup on final core.

Locks the accepted cleanup against ``django-trusts`` 1.x:

* requester FKs use ``settings.AUTH_USER_MODEL``
* stock core ``AuthorizedManager`` reaches registered handles
* team ceiling is ``Team.allowed_operations``
* repository-permission rows are the renamed three-FK relations
* organization membership is not a grant edge
"""

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from trusts.apps import configured_implementation_handles
from trusts.query import AuthorizedManager, AuthorizedQuerySet

from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import (
    Organization,
    Repository,
    Team,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)
from tests.fixtures import GhFixtureMixin


class Issue12ModelCleanupTest(SimpleTestCase):
    def test_requester_fields_use_auth_user_model(self):
        User = get_user_model()
        configured = apps.get_model(settings.AUTH_USER_MODEL)
        self.assertIs(User, configured)
        self.assertIs(Team._meta.get_field('members').related_model, User)
        self.assertIs(
            UserRepositoryPermission._meta.get_field('user').related_model,
            User,
        )

    def test_repository_manager_is_stock_core_authorized_manager(self):
        import gh_permissions.models as models

        self.assertIs(type(Repository.objects), AuthorizedManager)
        self.assertIs(
            type(Repository.objects.get_queryset()),
            AuthorizedQuerySet,
        )
        self.assertFalse(hasattr(models, 'GhAuthorizedQuerySet'))
        self.assertFalse(hasattr(models, 'GhAuthorizedManager'))

    def test_organization_does_not_persist_membership(self):
        self.assertFalse(hasattr(Organization, 'members'))
        field_names = {field.name for field in Organization._meta.get_fields()}
        self.assertNotIn('members', field_names)


class Issue12AuthorizedManagerHandleTest(GhFixtureMixin, TestCase):
    def test_stock_authorized_manager_reaches_registered_handles(self):
        handles = configured_implementation_handles()
        self.assertTrue(handles)
        self.assertEqual(
            [handle.path for handle in handles],
            [CANONICAL_BACKEND],
        )
        roots = [
            record.root
            for handle in handles
            for record in handle.registry.records
        ]
        self.assertEqual(
            roots,
            [UserRepositoryPermission, TeamRepositoryPermission],
        )
        self.assertIs(type(Repository.objects), AuthorizedManager)
        listed = list(
            Repository.objects.authorized(self.member, self.read).order_by('pk')
        )
        self.assertEqual(listed, [self.repo_a])
        listed = list(
            Repository.objects.authorized(
                self.collaborator, self.write,
            ).order_by('pk')
        )
        self.assertEqual(listed, [self.repo_b])
