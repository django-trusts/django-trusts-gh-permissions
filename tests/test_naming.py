"""GH vocabulary and public-relation contract."""

import inspect
from pathlib import Path

from django.test import SimpleTestCase, TestCase

from gh_permissions.models import (
    Account,
    AccountRepoGrant,
    Operation,
    Organization,
    PermissionBundle,
    Repository,
    Team,
    TeamRepoGrant,
)


FORBIDDEN_PUBLIC_RELATIONS = (
    'trusts', 'trustees', 'contexts', 'roles', 'groups',
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
            self.assertNotIn('trusts.core_backends', source)
            self.assertNotIn('from trusts.apps import kernel_config', source)
            self.assertNotIn('kernel_config(', source)

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


class GhRelationTest(TestCase):
    def test_advertised_public_relations(self):
        self.assertEqual(Account._meta.get_field('teams').related_model, Team)
        self.assertEqual(
            Account._meta.get_field('organizations').related_model,
            Organization,
        )
        self.assertEqual(
            Team._meta.get_field('permission_bundles').related_model,
            PermissionBundle,
        )
        self.assertEqual(
            Repository._meta.get_field('organization').related_model,
            Organization,
        )
        self.assertTrue(hasattr(Account, 'teams'))
        self.assertTrue(hasattr(Account, 'organizations'))
        self.assertTrue(hasattr(Team, 'permission_bundles'))

    def test_no_framework_or_zero_public_relations(self):
        for model in (
            Account, Organization, Team, PermissionBundle, Operation,
            Repository, TeamRepoGrant, AccountRepoGrant,
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
