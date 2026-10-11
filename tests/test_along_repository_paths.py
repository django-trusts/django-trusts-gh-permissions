"""Consumer proof for bounded repository-path review assignments (#46)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import TestCase

from trusts.apps import implementation_for_path
from trusts.policy_lock import _load_policy_sql_document, render_policy_sql_bytes

from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import (
    Organization,
    Repository,
    RepositoryPath,
    RepositoryPathGrant,
)
from gh_permissions.policy import register_repository_path_grant
from gh_permissions.services import ensure_owner_group
from tests.fixtures import isolated_handle


_REVIEW = 'gh_permissions.review_repositorypath'


def _review_permission():
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model='repositorypath',
        codename='review_repositorypath',
    )


class RepositoryPathAlongRegistrationTest(TestCase):
    def test_registration_is_zero_sql_shape_s_with_bound_eight(self):
        handle = isolated_handle()
        with self.assertNumQueries(0):
            record = register_repository_path_grant(handle)
        self.assertIs(record.root, RepositoryPathGrant)
        self.assertIs(record.content_model, RepositoryPath)
        self.assertEqual(record.user_path, ('user',))
        self.assertEqual(record.permission_path, ('permissions',))
        self.assertEqual(record.content_path, ('path',))
        self.assertEqual(record.along.path, ('path', 'parent'))
        self.assertEqual(record.along.shape, 'S')
        self.assertEqual(record.along.bound, 8)


class RepositoryPathAlongAuthorizationTest(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        owner_group = ensure_owner_group()
        self.organization = Organization.objects.create(
            name='path-org', owner_group=owner_group,
        )
        self.repository = Repository.objects.create(
            organization=self.organization, name='path-repo',
        )
        self.root = RepositoryPath.objects.create(
            repository=self.repository, name='/',
        )
        self.src = RepositoryPath.objects.create(parent=self.root, name='src')
        self.crypto = RepositoryPath.objects.create(
            parent=self.src, name='crypto',
        )
        self.leaf = RepositoryPath.objects.create(
            parent=self.crypto, name='check.py',
        )
        self.docs = RepositoryPath.objects.create(parent=self.root, name='docs')
        self.readme = RepositoryPath.objects.create(
            parent=self.docs, name='readme.md',
        )

        other_repository = Repository.objects.create(
            organization=self.organization, name='other-path-repo',
        )
        self.other_root = RepositoryPath.objects.create(
            repository=other_repository, name='/',
        )

        self.alice = User.objects.create_user('path-alice')
        self.bob = User.objects.create_user('path-bob')
        self.inactive = User.objects.create_user('path-inactive')
        self.inactive.is_active = False
        self.inactive.save(update_fields=('is_active',))
        self.review = _review_permission()

        self.grant = RepositoryPathGrant.objects.create(
            user=self.alice, path=self.src,
        )
        self.grant.permissions.add(self.review)
        inactive_grant = RepositoryPathGrant.objects.create(
            user=self.inactive, path=self.src,
        )
        inactive_grant.permissions.add(self.review)

    def test_directory_assignment_and_reverse_inquiry_agree(self):
        self.assertTrue(self.alice.has_perm(_REVIEW, self.src))
        self.assertTrue(self.alice.has_perm(_REVIEW, self.crypto))
        self.assertTrue(self.alice.has_perm(_REVIEW, self.leaf))
        self.assertFalse(self.alice.has_perm(_REVIEW, self.root))
        self.assertFalse(self.alice.has_perm(_REVIEW, self.readme))
        self.assertFalse(self.alice.has_perm(_REVIEW, self.other_root))
        self.assertFalse(self.bob.has_perm(_REVIEW, self.leaf))
        self.assertFalse(self.inactive.has_perm(_REVIEW, self.leaf))

        authorized = set(
            RepositoryPath.objects.authorized(self.alice, self.review)
        )
        self.assertEqual(authorized, {self.src, self.crypto, self.leaf})
        reverse = set(self.leaf.get_permitted_users(self.review))
        via_user_manager = set(
            get_user_model().objects.permitted(self.leaf, self.review)
        )
        self.assertEqual(reverse, {self.alice})
        self.assertEqual(via_user_manager, reverse)

    def test_bound_includes_eight_hops_and_denies_ninth(self):
        User = get_user_model()
        bounded = User.objects.create_user('path-bounded')
        grant = RepositoryPathGrant.objects.create(
            user=bounded, path=self.other_root,
        )
        grant.permissions.add(self.review)
        nodes = []
        parent = self.other_root
        for depth in range(1, 10):
            parent = RepositoryPath.objects.create(
                parent=parent, name='level-%d' % depth,
            )
            nodes.append(parent)

        self.assertTrue(bounded.has_perm(_REVIEW, nodes[7]))
        self.assertFalse(bounded.has_perm(_REVIEW, nodes[8]))
        self.assertIn(bounded, nodes[7].get_permitted_users(self.review))
        self.assertNotIn(bounded, nodes[8].get_permitted_users(self.review))

    def test_permission_and_row_removal_revoke_immediately(self):
        self.assertTrue(self.alice.has_perm(_REVIEW, self.leaf))
        self.grant.permissions.remove(self.review)
        self.assertFalse(self.alice.has_perm(_REVIEW, self.leaf))
        self.assertNotIn(
            self.alice, self.leaf.get_permitted_users(self.review),
        )

        self.grant.permissions.add(self.review)
        self.assertTrue(self.alice.has_perm(_REVIEW, self.leaf))
        self.grant.delete()
        self.assertFalse(self.alice.has_perm(_REVIEW, self.leaf))

    def test_policy_document_records_forward_and_reverse_recursive_sql(self):
        document = _load_policy_sql_document(render_policy_sql_bytes())
        content = next(
            item for item in document['backends'][0]['contents']
            if item['model'] == 'gh_permissions.RepositoryPath'
        )
        self.assertEqual(len(content['trusts']), 1)
        trust = content['trusts'][0]
        self.assertEqual(trust['root'], 'gh_permissions.RepositoryPathGrant')
        self.assertEqual(
            trust['along'],
            {'path': 'path__parent', 'shape': 'S', 'bound': 8},
        )
        for inquiry in (
            'permitted', 'has_perm', 'get_all_permissions',
            'get_permitted_users',
        ):
            rendered = content[inquiry]
            self.assertIn('WITH RECURSIVE', rendered['sql'].upper())
            self.assertIn({'const': 8}, rendered['params'])


class RepositoryPathShapeTest(TestCase):
    def test_root_and_child_store_one_unambiguous_tree_edge(self):
        owner_group = ensure_owner_group()
        organization = Organization.objects.create(
            name='shape-org', owner_group=owner_group,
        )
        repository = Repository.objects.create(
            organization=organization, name='shape-repo',
        )
        root = RepositoryPath.objects.create(
            repository=repository, name='/',
        )
        child = RepositoryPath.objects.create(parent=root, name='src')
        self.assertEqual(repository.root_path, root)
        self.assertIsNone(root.parent_id)
        self.assertIsNone(child.repository_id)
        self.assertEqual(child.parent_id, root.pk)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RepositoryPath.objects.create(name='orphan')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RepositoryPath.objects.create(
                    repository=repository, parent=root, name='both',
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RepositoryPath.objects.create(parent=root, name='src')


class RepositoryPathStartupTest(TestCase):
    def test_path_registration_is_the_last_ordinary_record(self):
        registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
            CANONICAL_BACKEND,
        ).registry
        record = registry.records[-1]
        self.assertIs(record.root, RepositoryPathGrant)
        self.assertEqual(record.along.path, ('path', 'parent'))
