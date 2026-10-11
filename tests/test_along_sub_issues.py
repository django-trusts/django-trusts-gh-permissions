"""Consumer proof for bounded GitHub-style sub-issue triage (#46)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import TestCase

from trusts.apps import implementation_for_path
from trusts.policy_lock import _load_policy_sql_document, render_policy_sql_bytes

from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import (
    Issue,
    IssuePermissions,
    Organization,
    Repository,
)
from gh_permissions.policy import register_issue_permissions
from gh_permissions.services import ensure_owner_group
from tests.fixtures import isolated_handle


_TRIAGE = 'gh_permissions.triage_issue'


def _triage_permission():
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model='issue',
        codename='triage_issue',
    )


class SubIssueAlongRegistrationTest(TestCase):
    def test_registration_is_zero_sql_shape_s_with_bound_eight(self):
        handle = isolated_handle()
        with self.assertNumQueries(0):
            record = register_issue_permissions(handle)
        self.assertIs(record.root, IssuePermissions)
        self.assertIs(record.content_model, Issue)
        self.assertEqual(record.user_path, ('user',))
        self.assertEqual(record.permission_path, ('permissions',))
        self.assertEqual(record.content_path, ('issue',))
        self.assertEqual(record.along.path, ('issue', 'parent'))
        self.assertEqual(record.along.shape, 'S')
        self.assertEqual(record.along.bound, 8)


class SubIssueAlongAuthorizationTest(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        owner_group = ensure_owner_group()
        self.organization = Organization.objects.create(
            name='issue-org', owner_group=owner_group,
        )
        self.repository = Repository.objects.create(
            organization=self.organization, name='issue-repo',
        )
        self.root = Issue.objects.create(
            repository=self.repository, number=100, title='Release 1.0',
        )
        self.assigned = Issue.objects.create(
            repository=self.repository,
            parent=self.root,
            number=101,
            title='Implement permissions',
        )
        self.leaf = Issue.objects.create(
            repository=self.repository,
            parent=self.assigned,
            number=102,
            title='Audit recursive policy',
        )
        self.sibling = Issue.objects.create(
            repository=self.repository,
            parent=self.root,
            number=103,
            title='Write release notes',
        )
        other_repository = Repository.objects.create(
            organization=self.organization, name='other-issue-repo',
        )
        self.other = Issue.objects.create(
            repository=other_repository, number=100, title='Other tree',
        )

        self.alice = User.objects.create_user('issue-alice')
        self.bob = User.objects.create_user('issue-bob')
        self.inactive = User.objects.create_user('issue-inactive')
        self.inactive.is_active = False
        self.inactive.save(update_fields=('is_active',))
        self.triage = _triage_permission()

        self.grant = IssuePermissions.objects.create(
            user=self.alice, issue=self.assigned,
        )
        self.grant.permissions.add(self.triage)
        inactive_grant = IssuePermissions.objects.create(
            user=self.inactive, issue=self.assigned,
        )
        inactive_grant.permissions.add(self.triage)

    def test_parent_assignment_and_reverse_inquiry_agree(self):
        self.assertTrue(self.alice.has_perm(_TRIAGE, self.assigned))
        self.assertTrue(self.alice.has_perm(_TRIAGE, self.leaf))
        self.assertFalse(self.alice.has_perm(_TRIAGE, self.root))
        self.assertFalse(self.alice.has_perm(_TRIAGE, self.sibling))
        self.assertFalse(self.alice.has_perm(_TRIAGE, self.other))
        self.assertFalse(self.bob.has_perm(_TRIAGE, self.leaf))
        self.assertFalse(self.inactive.has_perm(_TRIAGE, self.leaf))

        authorized = set(Issue.objects.authorized(self.alice, self.triage))
        self.assertEqual(authorized, {self.assigned, self.leaf})
        reverse = set(self.leaf.get_permitted_users(self.triage))
        via_user_manager = set(
            get_user_model().objects.permitted(self.leaf, self.triage)
        )
        self.assertEqual(reverse, {self.alice})
        self.assertEqual(via_user_manager, reverse)

    def test_reparenting_moves_effective_authority(self):
        self.assertTrue(self.alice.has_perm(_TRIAGE, self.leaf))
        self.leaf.parent = self.sibling
        self.leaf.save(update_fields=('parent',))
        self.assertFalse(self.alice.has_perm(_TRIAGE, self.leaf))
        self.assertNotIn(self.alice, self.leaf.get_permitted_users(self.triage))

        self.sibling.parent = self.assigned
        self.sibling.save(update_fields=('parent',))
        self.assertTrue(self.alice.has_perm(_TRIAGE, self.sibling))

    def test_bound_includes_eight_hops_and_denies_ninth(self):
        User = get_user_model()
        bounded = User.objects.create_user('issue-bounded')
        grant = IssuePermissions.objects.create(
            user=bounded, issue=self.other,
        )
        grant.permissions.add(self.triage)
        nodes = []
        parent = self.other
        for depth in range(1, 10):
            parent = Issue.objects.create(
                repository=self.other.repository,
                parent=parent,
                number=100 + depth,
                title='Level %d' % depth,
            )
            nodes.append(parent)

        self.assertTrue(bounded.has_perm(_TRIAGE, nodes[7]))
        self.assertFalse(bounded.has_perm(_TRIAGE, nodes[8]))
        self.assertIn(bounded, nodes[7].get_permitted_users(self.triage))
        self.assertNotIn(bounded, nodes[8].get_permitted_users(self.triage))

    def test_permission_and_row_removal_revoke_immediately(self):
        self.assertTrue(self.alice.has_perm(_TRIAGE, self.leaf))
        self.grant.permissions.remove(self.triage)
        self.assertFalse(self.alice.has_perm(_TRIAGE, self.leaf))
        self.assertNotIn(self.alice, self.leaf.get_permitted_users(self.triage))

        self.grant.permissions.add(self.triage)
        self.assertTrue(self.alice.has_perm(_TRIAGE, self.leaf))
        self.grant.delete()
        self.assertFalse(self.alice.has_perm(_TRIAGE, self.leaf))

    def test_policy_document_records_forward_and_reverse_recursive_sql(self):
        document = _load_policy_sql_document(render_policy_sql_bytes())
        content = next(
            item for item in document['backends'][0]['contents']
            if item['model'] == 'gh_permissions.Issue'
        )
        self.assertEqual(len(content['trusts']), 1)
        trust = content['trusts'][0]
        self.assertEqual(trust['root'], 'gh_permissions.IssuePermissions')
        self.assertEqual(
            trust['along'],
            {'path': 'issue__parent', 'shape': 'S', 'bound': 8},
        )
        for inquiry in (
            'permitted', 'has_perm', 'get_all_permissions',
            'get_permitted_users',
        ):
            rendered = content[inquiry]
            self.assertIn('WITH RECURSIVE', rendered['sql'].upper())
            self.assertIn({'const': 8}, rendered['params'])


class SubIssueShapeTest(TestCase):
    def test_repository_number_is_unique_and_parent_may_cross_repositories(self):
        owner_group = ensure_owner_group()
        organization = Organization.objects.create(
            name='shape-org', owner_group=owner_group,
        )
        repository = Repository.objects.create(
            organization=organization, name='shape-repo',
        )
        other_repository = Repository.objects.create(
            organization=organization, name='shape-repo-2',
        )
        parent = Issue.objects.create(
            repository=repository, number=1, title='Parent',
        )
        child = Issue.objects.create(
            repository=other_repository,
            parent=parent,
            number=1,
            title='Cross-repository child',
        )
        self.assertEqual(child.parent_id, parent.pk)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Issue.objects.create(
                    repository=repository, number=1, title='Duplicate number',
                )


class SubIssueStartupTest(TestCase):
    def test_issue_registration_is_the_last_ordinary_record(self):
        registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
            CANONICAL_BACKEND,
        ).registry
        record = registry.records[-1]
        self.assertIs(record.root, IssuePermissions)
        self.assertEqual(record.along.path, ('issue', 'parent'))
