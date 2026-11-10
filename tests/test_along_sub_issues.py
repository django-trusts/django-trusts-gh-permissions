"""Consumer proof for bounded GitHub-style sub-issue triage (#46)."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import TestCase

from gh_permissions.models import (
    Issue,
    IssueCloseGrant,
    IssuePermissions,
    Organization,
    Repository,
)
from gh_permissions.services import ensure_owner_group
from tests.fixtures import gh_policy_document, gh_trusts
from trusts.decorators import filter_by_permitted_content
from trusts.models import PermittedQuerySet


_TRIAGE = 'gh_permissions.triage_issue'
_ALONG = {
    'path': 'issue__parent',
    'shape': 'S',
    'bound': 8,
    'condition': (
        'reset_inheritance == False or reset_inheritance == None'
    ),
}


def _triage_permission():
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model='issue',
        codename='triage_issue',
    )


class SubIssueAlongRegistrationTest(TestCase):
    def test_issue_permissions_along_is_shape_s_bound_eight(self):
        model, trust = gh_trusts()[-1]
        self.assertEqual(trust['root'], IssuePermissions._meta.label)
        self.assertEqual(model, Issue._meta.label)
        self.assertEqual(trust['user']['path'], 'user')
        self.assertEqual(trust['permission']['path'], 'permissions')
        self.assertEqual(trust['content']['path'], 'issue')
        self.assertEqual(trust['along'], _ALONG)


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

    def test_draft_close_grant_has_directed_blockers_and_is_not_active(self):
        self.assigned.blocked_by.add(self.leaf)
        self.assertEqual(list(self.assigned.blocked_by.all()), [self.leaf])
        self.assertFalse(self.leaf.blocked_by.exists())
        self.assertFalse(self.leaf.is_closed)
        permission = Permission.objects.get(
            content_type__app_label='gh_permissions',
            content_type__model='issue',
            codename='close_issue',
        )
        IssueCloseGrant.objects.create(
            user=self.alice, issue=self.assigned, permission=permission,
        )
        self.assertFalse(self.alice.has_perm(
            'gh_permissions.close_issue', self.assigned,
        ))
        self.assertTrue(self.alice.has_perm(_TRIAGE, self.assigned))

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
        document = gh_policy_document()
        content = next(
            item for item in document['backends'][0]['contents']
            if item['model'] == 'gh_permissions.Issue'
        )
        self.assertEqual(len(content['trusts']), 1)
        trust = content['trusts'][0]
        self.assertEqual(trust['root'], 'gh_permissions.IssuePermissions')
        self.assertEqual(trust['along'], _ALONG)
        self.assertNotIn('filter_by_permitted_content', content)
        for inquiry in (
            'permitted', 'has_perm', 'get_all_permissions',
            'get_permitted_users',
        ):
            rendered = content[inquiry]
            sql = rendered['sql']
            self.assertIn('WITH RECURSIVE', sql.upper())
            self.assertIn('IS NULL', sql)
            self.assertIn('reset_inheritance', sql)
            self.assertNotIn('IS NOT TRUE', sql)
            self.assertIn('$along_bound=8', rendered['params'])


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
        model, trust = gh_trusts()[-1]
        self.assertEqual(model, Issue._meta.label)
        self.assertEqual(trust['root'], IssuePermissions._meta.label)
        self.assertEqual(trust['along'], _ALONG)


def _reached_indexes(grant_index, flags):
    """Indexes a grant reaches. The grant row is depth 0.

    A later issue is included while each step onto it sees
    ``reset_inheritance`` false or null. True stops that step. True on
    the grant row does not cut that grant or the steps below it.
    """
    reached = {grant_index}
    for index in range(grant_index + 1, len(flags)):
        if flags[index] is True:
            break
        reached.add(index)
    return reached


class SubIssueResetInheritanceTest(TestCase):
    """Null and false inherit. True cuts inherited steps. The bound still caps."""

    def setUp(self):
        super().setUp()
        User = get_user_model()
        owner_group = ensure_owner_group()
        organization = Organization.objects.create(
            name='reset-org', owner_group=owner_group,
        )
        self.repository = Repository.objects.create(
            organization=organization, name='reset-repo',
        )
        self.chain = []
        parent = None
        for number in range(1, 5):
            parent = Issue.objects.create(
                repository=self.repository,
                parent=parent,
                number=number,
                title='Level %d' % (number - 1),
            )
            self.chain.append(parent)
        self.triage = _triage_permission()
        self.user = User.objects.create_user('reset-user')

    def _set_flags(self, flags):
        for issue, flag in zip(self.chain, flags):
            issue.reset_inheritance = flag
            issue.save(update_fields=('reset_inheritance',))

    def _grant_at(self, index):
        IssuePermissions.objects.all().delete()
        grant = IssuePermissions.objects.create(
            user=self.user, issue=self.chain[index],
        )
        grant.permissions.add(self.triage)

    def _assert_reaches(self, indexes):
        reached = {self.chain[index] for index in indexes}
        permitted = set(
            PermittedQuerySet(model=Issue).permitted(self.triage, self.user)
        )
        permitted_code = set(
            PermittedQuerySet(model=Issue).permitted(_TRIAGE, self.user)
        )
        authorized = set(Issue.objects.authorized(self.user, self.triage))
        self.assertEqual(permitted, reached)
        self.assertEqual(permitted_code, reached)
        self.assertEqual(authorized, reached)
        # Single-hop content='issue' has no container on the path, so
        # filter_by_permitted_content has nothing to list. .permitted()
        # lists the walked issues.
        self.assertEqual(
            set(filter_by_permitted_content(
                Issue.objects.all(), self.user, self.triage, content=Issue,
            )),
            set(),
        )
        self.assertEqual(
            set(filter_by_permitted_content(
                Repository.objects.all(),
                self.user,
                self.triage,
                content=Issue,
            )),
            set(),
        )
        for issue in Issue.objects.all():
            holds = issue in reached
            self.assertEqual(
                self.user.has_perm(_TRIAGE, issue), holds, issue.number,
            )
            holders = set(issue.get_permitted_users(self.triage))
            self.assertEqual(self.user in holders, holds, issue.number)
            self.assertEqual(
                set(get_user_model().objects.permitted(issue, self.triage)),
                holders,
                issue.number,
            )
            self.assertEqual(
                _TRIAGE in self.user.get_all_permissions(issue),
                holds,
                issue.number,
            )

    def test_unset_column_stays_null(self):
        field = Issue._meta.get_field('reset_inheritance')
        self.assertIs(field.null, True)
        self.assertIs(field.has_default(), False)
        for issue in self.chain:
            self.assertIsNone(issue.reset_inheritance)

    def test_null_false_and_true_at_each_level_across_inquiries(self):
        for level in range(len(self.chain)):
            for flag in (None, False, True):
                with self.subTest(level=level, flag=flag):
                    flags = [None] * len(self.chain)
                    flags[level] = flag
                    self._set_flags(flags)
                    self._grant_at(0)
                    self._assert_reaches(_reached_indexes(0, flags))
                    self._grant_at(level)
                    self._assert_reaches(_reached_indexes(level, flags))

    def test_each_true_cuts_until_a_grant_on_that_issue(self):
        flags = [None, True, False, None]
        self._set_flags(flags)
        self._grant_at(0)
        self._assert_reaches(_reached_indexes(0, flags))
        self._grant_at(1)
        self._assert_reaches(_reached_indexes(1, flags))
        self._grant_at(3)
        self._assert_reaches(_reached_indexes(3, flags))

    def test_reset_stops_inside_the_bound(self):
        parent = self.chain[-1]
        nodes = list(self.chain)
        for depth in range(len(self.chain), 10):
            parent = Issue.objects.create(
                repository=self.repository,
                parent=parent,
                number=depth + 1,
                title='Level %d' % depth,
            )
            nodes.append(parent)
        nodes[4].reset_inheritance = True
        nodes[4].save(update_fields=('reset_inheritance',))
        self._grant_at(0)
        reached = set(nodes[:4])
        self.assertEqual(
            set(PermittedQuerySet(model=Issue).permitted(
                self.triage, self.user,
            )),
            reached,
        )
        self.assertTrue(self.user.has_perm(_TRIAGE, nodes[3]))
        self.assertFalse(self.user.has_perm(_TRIAGE, nodes[4]))
        self.assertFalse(self.user.has_perm(_TRIAGE, nodes[8]))
        self.assertIn(self.user, nodes[3].get_permitted_users(self.triage))
        self.assertNotIn(self.user, nodes[4].get_permitted_users(self.triage))
        self.assertNotIn(self.user, nodes[8].get_permitted_users(self.triage))
