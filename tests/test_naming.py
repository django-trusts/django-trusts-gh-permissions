"""GH vocabulary and public-relation contract."""

import inspect
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from gh_permissions.models import (
    Operation,
    Organization,
    Repository,
    Team,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)


FORBIDDEN_PUBLIC_RELATIONS = (
    'trusts', 'trustees', 'contexts', 'roles', 'groups',
)

REMOVED_MODELS = (
    'Account',
    'AccountRepoGrant',
    'GhAuthorizedManager',
    'GhAuthorizedQuerySet',
    'PermissionBundle',
    'TeamRepoGrant',
)


def _related_accessor_names(model):
    names = set()
    for field in model._meta.get_fields():
        names.add(field.name)
        related_name = getattr(field, 'related_name', None)
        if isinstance(related_name, str):
            names.add(related_name)
        if getattr(field, 'is_relation', False):
            remote = getattr(field, 'remote_field', None)
            if remote is not None:
                query_name = getattr(remote, 'related_name', None)
                if isinstance(query_name, str):
                    names.add(query_name)
            getter = getattr(field, 'related_query_name', None)
            if callable(getter):
                try:
                    qn = getter()
                except Exception:
                    qn = None
                if isinstance(qn, str):
                    names.add(qn)
    return names


class GhNamingTest(SimpleTestCase):
    def test_package_source_uses_gh_not_full_external_name(self):
        import gh_permissions
        import gh_permissions.apps
        import gh_permissions.backends
        import gh_permissions.models
        import gh_permissions.policy
        for module in (
            gh_permissions, gh_permissions.apps, gh_permissions.backends,
            gh_permissions.models, gh_permissions.policy,
        ):
            source = Path(inspect.getfile(module)).read_text()
            self.assertNotIn('GitHub', source)
            self.assertNotIn('github', source.lower())
            self.assertNotIn('trusts.zero', source)
            self.assertNotIn('django-trusts-zero', source)

    def test_no_abandoned_framework_glue_imports(self):
        import gh_permissions.policy as policy
        source = Path(inspect.getfile(policy)).read_text()
        self.assertNotIn('trusts.context', source)
        self.assertNotIn('trusts.trustee', source)
        self.assertNotIn('TrustModelBackend', source)
        self.assertIn('register_direct', source)
        self.assertIn('register_team', source)
        self.assertNotIn('register_gh_policy', source)
        self.assertIn('permission_in', source)
        self.assertIn('Equal', source)
        self.assertIn('All', source)

    def test_obsolete_queryset_and_account_names_are_gone(self):
        import gh_permissions.models as models
        for name in REMOVED_MODELS:
            self.assertFalse(hasattr(models, name), name)


class GhRelationTest(TestCase):
    def test_advertised_public_relations(self):
        User = get_user_model()
        self.assertEqual(Team._meta.get_field('members').related_model, User)
        self.assertEqual(
            Team._meta.get_field('allowed_operations').related_model,
            Operation,
        )
        self.assertEqual(
            Repository._meta.get_field('organization').related_model,
            Organization,
        )
        self.assertIs(
            UserRepositoryPermission._meta.get_field('user').related_model,
            apps.get_model(settings.AUTH_USER_MODEL),
        )
        self.assertIs(User, apps.get_model(settings.AUTH_USER_MODEL))
        self.assertTrue(hasattr(Team, 'members'))
        self.assertTrue(hasattr(Team, 'allowed_operations'))
        self.assertFalse(hasattr(Organization, 'members'))

    def test_no_framework_or_zero_public_relations(self):
        for model in (
            Organization, Team, Operation, Repository,
            TeamRepositoryPermission, UserRepositoryPermission,
        ):
            leaked = _related_accessor_names(model).intersection(
                FORBIDDEN_PUBLIC_RELATIONS,
            )
            self.assertFalse(
                leaked,
                '%s exposes forbidden public relations %s' % (
                    model._meta.label, sorted(leaked),
                ),
            )
            for name in FORBIDDEN_PUBLIC_RELATIONS:
                self.assertFalse(hasattr(model, name))
