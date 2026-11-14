"""GH vocabulary and public-relation contract."""

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import FieldDoesNotExist
from django.test import TestCase

from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
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


class GhRelationTest(TestCase):
    def test_advertised_public_relations(self):
        User = get_user_model()
        configured = apps.get_model(settings.AUTH_USER_MODEL)
        self.assertIs(User, configured)
        self.assertEqual(User._meta.get_field('teams').related_model, Team)
        self.assertEqual(
            Team._meta.get_field('allowed_operations').related_model,
            Permission,
        )
        self.assertEqual(
            RepositoryCollaborator._meta.get_field('permissions').related_model,
            Permission,
        )
        self.assertEqual(
            TeamRepositoryPermission._meta.get_field('operation').related_model,
            Permission,
        )
        self.assertEqual(
            Repository._meta.permissions,
            (
                ('read_repository', 'Can read repository'),
                ('write_repository', 'Can write repository'),
                ('admin_repository', 'Can administer repository'),
            ),
        )
        self.assertEqual(
            Organization._meta.permissions,
            (('manage_organization', 'Can manage organization'),),
        )
        self.assertEqual(
            Organization._meta.get_field('owner_group').related_model,
            Group,
        )
        self.assertEqual(
            Team._meta.get_field('members').related_model,
            User,
        )
        self.assertEqual(
            RepositoryCollaborator._meta.get_field('user').related_model,
            User,
        )
        owners = Organization._meta.get_field('owners')
        self.assertTrue(owners.many_to_many)
        self.assertIs(owners.remote_field.through, OrganizationOwnership)
        self.assertEqual(
            OrganizationOwnership._meta.get_field('user').related_model,
            User,
        )
        self.assertEqual(
            Repository._meta.get_field('organization').related_model,
            Organization,
        )
        self.assertTrue(hasattr(User, 'teams'))
        self.assertTrue(hasattr(Team, 'allowed_operations'))
        user_field = RepositoryCollaborator._meta.get_field('user')
        member_field = Team._meta.get_field('members')
        self.assertIs(apps.get_model(user_field.deconstruct()[3]['to']), configured)
        self.assertIs(apps.get_model(member_field.deconstruct()[3]['to']), configured)
        self.assertTrue(user_field.swappable)
        self.assertEqual(user_field.swappable_setting, 'AUTH_USER_MODEL')
        self.assertTrue(member_field.swappable)
        self.assertEqual(member_field.swappable_setting, 'AUTH_USER_MODEL')
        with self.assertRaises(FieldDoesNotExist):
            User._meta.get_field('organizations')
        self.assertFalse(hasattr(Organization, 'members'))

    def test_alias_is_not_an_authorization_edge(self):
        relations = [
            field for field in Alias._meta.get_fields()
            if getattr(field, 'is_relation', False)
        ]
        self.assertEqual(relations, [])
        self.assertTrue(Alias._meta.get_field('name').unique)

    def test_no_framework_or_zero_public_relations(self):
        for model in (
            Alias, Organization, OrganizationOwnership, Team, Repository,
            TeamRepositoryPermission, RepositoryCollaborator,
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
