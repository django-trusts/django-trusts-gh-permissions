"""Reverse inquiry agrees with has_perm and with authorized listings.

``authorized`` takes the ``auth.Permission`` instance. ``has_perm`` and
``permitted`` accept the Django permission string as the alias. The
example user manager supplies ``User.objects.permitted``. ``Repository``
supplies ``get_permitted_users``.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from gh_permissions.models import Repository
from tests.fixtures import GhFixtureMixin


_READ = 'gh_permissions.read_repository'
_WRITE = 'gh_permissions.write_repository'


class PermittedUsersAgreementTest(GhFixtureMixin, TestCase):
    def test_library_does_not_import_the_example_user(self):
        import gh_permissions.models as models
        from pathlib import Path
        import inspect

        source = Path(inspect.getfile(models)).read_text()
        self.assertNotIn('example.User', source)
        self.assertNotIn('example.models', source)
        self.assertTrue(hasattr(get_user_model().objects, 'permitted'))
        self.assertTrue(hasattr(self.repo_a, 'get_permitted_users'))
        self.assertFalse(hasattr(Repository.objects, 'permitted'))

    def test_direct_and_team_grants_agree_across_the_four_spellings(self):
        User = get_user_model()
        cases = (
            (self.repo_a, self.read, _READ),
            (self.repo_b, self.write, _WRITE),
        )
        for repository, permission, code in cases:
            with self.assertNumQueries(0):
                via_manager = User.objects.permitted(repository, code)
                via_instance = User.objects.permitted(repository, permission)
                via_content = repository.get_permitted_users(code)
                via_content_instance = repository.get_permitted_users(permission)
            with self.assertNumQueries(1):
                manager_ids = set(via_manager.values_list('pk', flat=True))
            with self.assertNumQueries(1):
                instance_ids = set(via_instance.values_list('pk', flat=True))
            with self.assertNumQueries(1):
                content_ids = set(via_content.values_list('pk', flat=True))
            with self.assertNumQueries(1):
                content_instance_ids = set(
                    via_content_instance.values_list('pk', flat=True)
                )
            self.assertEqual(manager_ids, instance_ids)
            self.assertEqual(manager_ids, content_ids)
            self.assertEqual(manager_ids, content_instance_ids)

            candidates = User.objects.filter(is_superuser=False)
            for user in candidates:
                granted = user.has_perm(code, repository)
                self.assertEqual(user.pk in manager_ids, granted)
                with self.assertNumQueries(1):
                    listed = repository in list(
                        Repository.objects.authorized(user, permission)
                    )
                self.assertEqual(listed, granted)

    def test_team_ceiling_keeps_the_member_off_the_write_listing(self):
        User = get_user_model()
        with self.assertNumQueries(1):
            readers = set(
                self.repo_a.get_permitted_users(self.read)
                .values_list('pk', flat=True)
            )
        self.assertIn(self.member.pk, readers)
        self.assertNotIn(self.collaborator.pk, readers)
        self.assertNotIn(
            self.member.pk,
            set(
                User.objects.permitted(self.repo_a, _WRITE)
                .values_list('pk', flat=True)
            ),
        )
        self.assertTrue(self.member.has_perm(_READ, self.repo_a))
        self.assertFalse(self.member.has_perm(_WRITE, self.repo_a))

    def test_revocation_drops_the_user_from_both_adapters(self):
        from gh_permissions.models import UserRepositoryPermission

        User = get_user_model()
        self.assertIn(
            self.collaborator,
            set(User.objects.permitted(self.repo_b, _WRITE)),
        )
        UserRepositoryPermission.objects.filter(
            user=self.collaborator, repository=self.repo_b, operation=self.write,
        ).delete()
        self.assertNotIn(
            self.collaborator,
            set(self.repo_b.get_permitted_users(_WRITE)),
        )
        self.assertFalse(self.collaborator.has_perm(_WRITE, self.repo_b))
        self.assertEqual(
            list(Repository.objects.authorized(self.collaborator, self.write)),
            [],
        )

    def test_inactive_user_and_active_superuser_follow_has_perm(self):
        User = get_user_model()
        self.member.is_active = False
        self.member.save()
        self.assertFalse(self.member.has_perm(_READ, self.repo_a))
        self.assertNotIn(
            self.member,
            set(User.objects.permitted(self.repo_a, self.read)),
        )
        self.assertEqual(
            list(Repository.objects.authorized(self.member, self.read)),
            [self.repo_a],
        )

        superuser = User.objects.create_superuser(
            username='super', password='secret', email='super@example.com',
        )
        self.assertTrue(superuser.has_perm(_READ, self.repo_a))
        self.assertIn(
            superuser,
            set(self.repo_a.get_permitted_users(_READ)),
        )
        self.assertNotIn(
            self.repo_a,
            set(Repository.objects.authorized(superuser, self.read)),
        )
