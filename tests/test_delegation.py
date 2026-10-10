"""Concrete GH proof of one-level correlated delegation (#43)."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from trusts.policy_lock import _load_policy_sql_document, render_policy_sql_bytes

from gh_permissions.models import (
    AllPersonalRepositoriesDelegation,
    Organization,
    OrganizationOwnership,
    PersonalRepositoryDelegation,
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
        self.personal_organization = Organization.objects.create(
            personal_user=self.sponsor, owner_group=self.owner_group,
        )
        self.personal_ownership = OrganizationOwnership.objects.create(
            user=self.sponsor, organization=self.personal_organization,
        )
        self.personal_repository = Repository.objects.create(
            organization=self.personal_organization, name='personal-repo',
        )
        self.second_personal_repository = Repository.objects.create(
            organization=self.personal_organization,
            name='second-personal-repo',
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

    def _select_personal(self, *, repository=None, sponsor=None):
        delegation = PersonalRepositoryDelegation.objects.create(
            delegate=self.delegate,
            sponsor=sponsor or self.sponsor,
            repository=repository or self.personal_repository,
        )
        delegation.allowed_permissions.add(self.read)
        return delegation

    def _select_all_personal(self, *, sponsor=None):
        delegation = AllPersonalRepositoriesDelegation.objects.create(
            delegate=self.delegate,
            sponsor=sponsor or self.sponsor,
        )
        delegation.allowed_permissions.add(self.read)
        return delegation

    def _assert_all_projections(
        self, expected, permission=None, *, delegate=None, repository=None,
    ):
        User = get_user_model()
        permission = permission or self.read
        delegate = delegate or self.delegate
        repository = repository or self.repository
        code = 'gh_permissions.%s' % permission.codename
        self.assertIs(delegate.has_perm(code, repository), expected)
        self.assertEqual(
            repository in set(
                Repository.objects.authorized(delegate, permission)
            ),
            expected,
        )
        self.assertEqual(
            code in delegate.get_all_permissions(repository),
            expected,
        )
        self.assertEqual(
            delegate in set(User.objects.permitted(repository, code)),
            expected,
        )
        self.assertEqual(
            delegate in set(repository.get_permitted_users(code)),
            expected,
        )

    def test_three_delegation_paths_or_together_for_repository(self):
        self._select_personal()

        # A named personal delegation covers only its selected repository.
        self._assert_all_projections(
            True, repository=self.personal_repository,
        )

        self._assert_all_projections(
            False, repository=self.second_personal_repository,
        )

        organization_delegation = self._select()
        self._assert_all_projections(False)
        self._approve(organization_delegation)
        self._assert_all_projections(True)

        # The no-repository-FK relationship reaches all repositories through
        # sponsor.personal_organization.repositories.
        self._select_all_personal()
        self._assert_all_projections(
            True, repository=self.second_personal_repository,
        )

        # All three roots remain independently effective for the shared
        # Repository content model.
        self._assert_all_projections(
            True, repository=self.personal_repository,
        )

        self._assert_all_projections(True)

    def test_all_personal_path_does_not_cover_conventional_org_repositories(self):
        self._select_all_personal()

        # The sponsor ordinarily owns both organizations. The indirect
        # content path still reaches only the sponsor's personal organization.
        self.assertTrue(self.sponsor.has_perm(_READ, self.repository))
        self._assert_all_projections(False)
        self._assert_all_projections(
            True, repository=self.personal_repository,
        )

        self.owner_group.permissions.remove(self.read)
        self.assertFalse(
            self.sponsor.has_perm(_READ, self.personal_repository)
        )
        self._assert_all_projections(
            False, repository=self.personal_repository,
        )

    def test_personal_path_cannot_bypass_organization_approval(self):
        self._select_personal(repository=self.repository)
        organization_delegation = self._select()

        # The personal row's sponsor has ordinary authority, but its left
        # side fails because this is not that sponsor's personal repository.
        # The organization row cannot borrow the personal path's lack of an
        # approval requirement.
        self._assert_all_projections(False)

        self._approve(organization_delegation)
        self._assert_all_projections(True)

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
        team_grant = TeamRepositoryPermission.objects.create(
            team=team, repository=self.repository, operation=self.read,
        )
        self.assertTrue(self.sponsor.has_perm(_READ, self.repository))
        self.assertTrue(self.delegate.has_perm(_READ, self.repository))

        # The bridge and approval remain, but no ordinary sponsor path does.
        team_grant.delete()
        self.assertFalse(self.sponsor.has_perm(_READ, self.repository))
        self._assert_all_projections(False)

    def test_ceiling_cannot_exceed_the_sponsors_live_authority(self):
        delegation = self._select()
        delegation.allowed_permissions.add(self.write)
        self._approve(delegation)
        self._assert_all_projections(True, self.write)

        self.owner_group.permissions.remove(self.write)

        self.assertFalse(self.sponsor.has_perm(_WRITE, self.repository))
        self._assert_all_projections(False, self.write)

    def test_sponsor_grant_on_another_repository_cannot_cross_correlate(self):
        delegation = self._select()
        self._approve(delegation)
        self._assert_all_projections(True)

        self.owner_group.permissions.remove(self.read)
        collaboration = RepositoryCollaborator.objects.create(
            user=self.sponsor, repository=self.other_repository,
        )
        collaboration.permissions.add(self.read)

        self.assertTrue(self.sponsor.has_perm(_READ, self.other_repository))
        self.assertFalse(self.sponsor.has_perm(_READ, self.repository))
        self._assert_all_projections(False)

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
        self.assertEqual(len(repository['delegations']), 3)
        delegations = {
            delegation['root']: delegation
            for delegation in repository['delegations']
        }
        delegation = delegations['gh_permissions.RepositoryDelegation']
        self.assertEqual(
            delegation['root'], 'gh_permissions.RepositoryDelegation',
        )
        self.assertEqual(
            delegation['delegate']['path'], 'delegate',
        )
        self.assertEqual(
            delegation['sponsor']['path'], 'sponsor_ownership__user',
        )
        personal = delegations[
            'gh_permissions.PersonalRepositoryDelegation'
        ]
        self.assertEqual(personal['delegate']['path'], 'delegate')
        self.assertEqual(personal['sponsor']['path'], 'sponsor')
        all_personal = delegations[
            'gh_permissions.AllPersonalRepositoriesDelegation'
        ]
        self.assertEqual(all_personal['delegate']['path'], 'delegate')
        self.assertEqual(all_personal['sponsor']['path'], 'sponsor')
        self.assertEqual(
            all_personal['content']['path'],
            'sponsor__personal_organization__repositories',
        )

        for inquiry in (
            'permitted', 'has_perm', 'get_all_permissions',
            'get_permitted_users',
        ):
            sql = repository[inquiry]['sql'].lower()
            with self.subTest(inquiry=inquiry):
                # Three sibling delegation roots are ORed. Each root keeps its
                # own left predicates and its own ordinary-only sponsor union.
                self.assertEqual(
                    sql.count('gh_permissions_repositorydelegation"'), 1,
                )
                self.assertEqual(
                    sql.count(
                        'gh_permissions_personalrepositorydelegation"'
                    ),
                    1,
                )
                self.assertEqual(
                    sql.count(
                        'gh_permissions_allpersonalrepositoriesdelegation"'
                    ),
                    1,
                )
                self.assertEqual(
                    sql.count('gh_permissions_repositorycollaborator"'), 4,
                )
                self.assertEqual(
                    sql.count('gh_permissions_teamrepositorypermission"'), 4,
                )
                self.assertEqual(
                    sql.count('gh_permissions_organizationownership"'), 5,
                )
                personal_start = sql.index(
                    'gh_permissions_personalrepositorydelegation"'
                )
                all_personal_start = sql.index(
                    'gh_permissions_allpersonalrepositoriesdelegation"'
                )
                organization_start = sql.index(
                    'gh_permissions_repositorydelegation"'
                )
                personal_branch = sql[
                    personal_start:all_personal_start
                ]
                all_personal_branch = sql[
                    all_personal_start:organization_start
                ]
                organization_branch = sql[organization_start:]
                self.assertIn('personal_user_id', personal_branch)
                self.assertIn(
                    'personal_user_id', all_personal_branch,
                )
                self.assertNotIn(
                    'approved_organization_id', personal_branch,
                )
                self.assertNotIn(
                    'approved_organization_id', all_personal_branch,
                )
                self.assertIn(
                    'approved_organization_id', organization_branch,
                )
                self.assertIn('approved_organization_id', sql)
                self.assertIn('sponsor_ownership_id', sql)

        with open(settings.TRUSTS_POLICY_LOCKFILE, 'rb') as lockfile:
            self.assertEqual(lockfile.read(), render_policy_sql_bytes())
