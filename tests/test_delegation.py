"""Concrete GH proof of one-level correlated delegation (#43)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from trusts.policy_lock import _load_policy_sql_document, render_policy_sql_bytes

from gh_permissions.models import (
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    RepositoryDelegation,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import ensure_owner_group
from tests.fixtures import repository_permission


_READ = 'gh_permissions.read_repository'
_WRITE = 'gh_permissions.write_repository'


class RepositoryDelegationTest(TestCase):
    def setUp(self):
        super().setUp()
        User = get_user_model()
        self.owner_group = ensure_owner_group()
        self.organization = Organization.objects.create(
            name='delegated-org', owner_group=self.owner_group,
        )
        self.other_organization = Organization.objects.create(
            name='other-delegated-org', owner_group=self.owner_group,
        )
        self.repository = Repository.objects.create(
            organization=self.organization, name='delegated-repo',
        )
        self.other_repository = Repository.objects.create(
            organization=self.other_organization, name='other-repo',
        )
        self.sponsor = User.objects.create_user('sponsor')
        self.delegate = User.objects.create_user('delegate')
        self.second_delegate = User.objects.create_user('second-delegate')
        self.approver = User.objects.create_user('approver')
        self.sponsor_ownership = OrganizationOwnership.objects.create(
            user=self.sponsor, organization=self.organization,
        )
        self.read = repository_permission('read_repository')
        self.write = repository_permission('write_repository')

    def _select(self, *, delegate=None, ownership=None, repository=None):
        delegation = RepositoryDelegation.objects.create(
            delegate=delegate or self.delegate,
            sponsor_ownership=ownership or self.sponsor_ownership,
            repository=repository or self.repository,
        )
        delegation.allowed_permissions.add(self.read)
        return delegation

    def _approve(self, delegation, organization=None):
        delegation.approved_organization = organization or self.organization
        delegation.approved_by = self.approver
        delegation.save(
            update_fields=('approved_organization', 'approved_by'),
        )
        return delegation

    def _assert_all_projections(self, expected):
        User = get_user_model()
        self.assertIs(self.delegate.has_perm(_READ, self.repository), expected)
        self.assertEqual(
            self.repository in set(
                Repository.objects.authorized(self.delegate, self.read)
            ),
            expected,
        )
        self.assertEqual(
            _READ in self.delegate.get_all_permissions(self.repository),
            expected,
        )
        self.assertEqual(
            self.delegate in set(User.objects.permitted(self.repository, _READ)),
            expected,
        )
        self.assertEqual(
            self.delegate in set(self.repository.get_permitted_users(_READ)),
            expected,
        )

    def test_selection_approval_scope_and_sponsor_grant_are_all_required(self):
        delegation = self._select()
        self._assert_all_projections(False)

        self._approve(delegation)
        self._assert_all_projections(True)
        self.assertFalse(self.delegate.has_perm(_WRITE, self.repository))

        delegation.allowed_permissions.remove(self.read)
        self._assert_all_projections(False)

    def test_one_delegation_uses_the_union_of_ordinary_sponsor_paths(self):
        delegation = self._select()
        self._approve(delegation)

        # Organization owner path.
        self.assertTrue(self.sponsor.has_perm(_READ, self.repository))
        self.assertTrue(self.delegate.has_perm(_READ, self.repository))

        # The same delegation survives through the direct path.
        self.owner_group.permissions.remove(self.read)
        self.assertFalse(self.sponsor.has_perm(_READ, self.repository))
        collaboration = RepositoryCollaborator.objects.create(
            user=self.sponsor, repository=self.repository,
        )
        collaboration.permissions.add(self.read)
        self.assertTrue(self.sponsor.has_perm(_READ, self.repository))
        self.assertTrue(self.delegate.has_perm(_READ, self.repository))

        # The same delegation also survives through the team path.
        collaboration.delete()
        team = Team.objects.create(
            organization=self.organization, name='delegating-team',
        )
        team.members.add(self.sponsor)
        team.allowed_operations.add(self.read)
        TeamRepositoryPermission.objects.create(
            team=team, repository=self.repository, operation=self.read,
        )
        self.assertTrue(self.sponsor.has_perm(_READ, self.repository))
        self.assertTrue(self.delegate.has_perm(_READ, self.repository))

    def test_removing_sponsor_ownership_revokes_even_if_direct_grant_remains(self):
        delegation = self._select()
        self._approve(delegation)
        collaboration = RepositoryCollaborator.objects.create(
            user=self.sponsor, repository=self.repository,
        )
        collaboration.permissions.add(self.read)

        self.sponsor_ownership.delete()

        self.assertTrue(self.sponsor.has_perm(_READ, self.repository))
        self.assertFalse(self.delegate.has_perm(_READ, self.repository))
        self.assertFalse(
            RepositoryDelegation.objects.filter(pk=delegation.pk).exists()
        )

    def test_approval_and_organization_alignment_cannot_be_borrowed(self):
        wrong_approval = self._select()
        self._approve(wrong_approval, organization=self.other_organization)
        self.assertFalse(self.delegate.has_perm(_READ, self.repository))

        other_owner = OrganizationOwnership.objects.create(
            user=self.sponsor, organization=self.other_organization,
        )
        wrong_sponsor_scope = self._select(
            delegate=self.second_delegate,
            ownership=other_owner,
        )
        self._approve(wrong_sponsor_scope)
        self.assertFalse(
            self.second_delegate.has_perm(_READ, self.repository)
        )

    def test_delegated_authority_cannot_sponsor_a_second_level(self):
        first = self._select()
        self._approve(first)
        self.owner_group.permissions.remove(self.read)
        direct = RepositoryCollaborator.objects.create(
            user=self.sponsor, repository=self.repository,
        )
        direct.permissions.add(self.read)
        self.assertTrue(self.delegate.has_perm(_READ, self.repository))

        delegate_ownership = OrganizationOwnership.objects.create(
            user=self.delegate, organization=self.organization,
        )
        second = self._select(
            delegate=self.second_delegate,
            ownership=delegate_ownership,
        )
        self._approve(second)
        self.assertFalse(
            self.second_delegate.has_perm(_READ, self.repository)
        )

    def test_inactive_delegate_or_sponsor_fails_closed(self):
        delegation = self._select()
        self._approve(delegation)
        self.delegate.is_active = False
        self.delegate.save(update_fields=('is_active',))
        self._assert_all_projections(False)

        self.delegate.is_active = True
        self.delegate.save(update_fields=('is_active',))
        self.sponsor.is_active = False
        self.sponsor.save(update_fields=('is_active',))
        self._assert_all_projections(False)

    def test_policy_lock_shows_left_requirements_and_ordinary_inner_union(self):
        document = _load_policy_sql_document(render_policy_sql_bytes())
        repository = next(
            content
            for content in document['backends'][0]['contents']
            if content['model'] == 'gh_permissions.Repository'
        )
        self.assertEqual(len(repository['delegations']), 1)
        delegation = repository['delegations'][0]
        self.assertEqual(
            delegation['root'], 'gh_permissions.RepositoryDelegation',
        )
        self.assertEqual(
            delegation['delegate']['path'], 'delegate',
        )
        self.assertEqual(
            delegation['sponsor']['path'], 'sponsor_ownership__user',
        )

        for inquiry in (
            'permitted', 'has_perm', 'get_all_permissions',
            'get_permitted_users',
        ):
            sql = repository[inquiry]['sql'].lower()
            with self.subTest(inquiry=inquiry):
                # Exactly one delegation layer. Its inner union contains all
                # three ordinary GH roots, not delegated records.
                self.assertEqual(
                    sql.count('gh_permissions_repositorydelegation"'), 1,
                )
                self.assertGreaterEqual(
                    sql.count('gh_permissions_repositorycollaborator"'), 2,
                )
                self.assertGreaterEqual(
                    sql.count('gh_permissions_teamrepositorypermission"'), 2,
                )
                self.assertGreaterEqual(
                    sql.count('gh_permissions_organizationownership"'), 3,
                )
                self.assertIn('approved_organization_id', sql)
                self.assertIn('sponsor_ownership_id', sql)

        with open('trusts-policy.lock.yaml', 'rb') as lockfile:
            self.assertEqual(lockfile.read(), render_policy_sql_bytes())
