"""Permission-string enumeration follows the mixin once the terminal is real.

``GhAuthorizationBackend`` does not override ``get_all_permissions`` or
``get_group_permissions``. ``_perm_codes`` reads ``auth.Permission``
``content_type__app_label`` and ``codename``.
"""

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings

from trusts.apps import implementation_for_path
from trusts.backends import TrustModelBackendMixin

from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.backends import GhAuthorizationBackend
from gh_permissions.models import Repository
from tests.fixtures import GhFixtureMixin


_OTHER_ALL = {'other.view_repository'}
_OTHER_GROUP = {'other.change_repository'}
_READ = 'gh_permissions.read_repository'


class StringContributingBackend(object):
    """Test double that contributes Django permission strings."""

    def get_all_permissions(self, user_obj, obj=None):
        return set(_OTHER_ALL)

    def get_group_permissions(self, user_obj, obj=None):
        return set(_OTHER_GROUP)


def _registry():
    return implementation_for_path(CANONICAL_BACKEND).configured_backend(
        CANONICAL_BACKEND,
    ).registry


class GhPermissionStringEnumerationTest(GhFixtureMixin, TestCase):
    def test_backend_enumeration_uses_the_mixin_perm_codes(self):
        self.assertIs(
            GhAuthorizationBackend.get_all_permissions,
            TrustModelBackendMixin.get_all_permissions,
        )
        self.assertIs(
            GhAuthorizationBackend.get_group_permissions,
            TrustModelBackendMixin.get_group_permissions,
        )
        backend = GhAuthorizationBackend()
        self.assertEqual(
            backend.get_all_permissions(self.member, self.repo_a),
            {_READ},
        )
        self.assertEqual(
            backend.get_group_permissions(self.member, self.repo_a),
            set(),
        )
        queryset = Repository.objects.filter(pk=self.repo_a.pk)
        self.assertEqual(
            backend.get_all_permissions(self.member, queryset),
            {_READ},
        )
        self.assertEqual(
            backend.get_group_permissions(self.member, queryset),
            set(),
        )

    def test_backend_enumeration_without_object_is_empty(self):
        backend = GhAuthorizationBackend()
        self.assertEqual(backend.get_all_permissions(self.member, None), set())
        self.assertEqual(backend.get_all_permissions(self.member), set())
        self.assertEqual(
            backend.get_group_permissions(self.member, None),
            set(),
        )
        self.assertEqual(backend.get_group_permissions(self.member), set())

    def test_user_enumeration_returns_the_granted_codename(self):
        self.assertEqual(self.member.get_all_permissions(self.repo_a), {_READ})
        self.assertEqual(self.member.get_group_permissions(self.repo_a), set())
        self.assertEqual(self.member.get_all_permissions(), set())
        self.assertEqual(self.member.get_group_permissions(), set())

    @override_settings(AUTHENTICATION_BACKENDS=(
        'gh_permissions.backends.GhAuthorizationBackend',
        'tests.test_issue20.StringContributingBackend',
    ))
    def test_user_enumeration_unions_other_backends_only(self):
        self.assertEqual(
            self.member.get_all_permissions(self.repo_a),
            _OTHER_ALL | {_READ},
        )
        self.assertEqual(
            self.member.get_group_permissions(self.repo_a),
            _OTHER_GROUP,
        )
        self.assertEqual(self.member.get_all_permissions(None), _OTHER_ALL)
        self.assertEqual(self.member.get_group_permissions(None), _OTHER_GROUP)

    def test_anonymous_and_inactive_enumeration_is_empty(self):
        backend = GhAuthorizationBackend()
        anonymous = AnonymousUser()
        self.assertEqual(
            backend.get_all_permissions(anonymous, self.repo_a),
            set(),
        )
        self.assertEqual(
            backend.get_group_permissions(anonymous, self.repo_a),
            set(),
        )
        self.assertEqual(backend.get_all_permissions(anonymous, None), set())
        self.assertEqual(
            backend.get_group_permissions(anonymous, None),
            set(),
        )
        self.assertEqual(anonymous.get_all_permissions(self.repo_a), set())
        self.assertEqual(anonymous.get_group_permissions(self.repo_a), set())

        self.member.is_active = False
        self.member.save()
        self.assertEqual(
            backend.get_all_permissions(self.member, self.repo_a),
            set(),
        )
        self.assertEqual(
            backend.get_group_permissions(self.member, self.repo_a),
            set(),
        )
        self.assertEqual(self.member.get_all_permissions(self.repo_a), set())
        self.assertEqual(self.member.get_group_permissions(self.repo_a), set())
        self.assertEqual(backend.get_all_permissions(self.member, None), set())
        self.assertEqual(
            backend.get_group_permissions(self.member, None),
            set(),
        )

    def test_supported_registry_paths_still_authorize(self):
        registry = _registry()
        self.assertTrue(
            registry.has_permission(self.member, self.repo_a, self.read),
        )
        enumerated = {
            row.pk for row in registry.permissions_for(self.member, self.repo_a)
        }
        self.assertIn(self.read.pk, enumerated)
        self.assertEqual(
            list(
                Repository.objects.authorized(self.member, self.read)
                .order_by('pk')
            ),
            [self.repo_a],
        )
        backend = GhAuthorizationBackend()
        self.assertEqual(
            backend.get_all_permissions(self.member, self.repo_a),
            {_READ},
        )
        self.assertTrue(
            registry.has_permission(self.member, self.repo_a, self.read),
        )
