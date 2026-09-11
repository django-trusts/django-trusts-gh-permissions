"""#9: user-facing README and package metadata for the GH reference."""

from pathlib import Path

from django.test import SimpleTestCase, TestCase

from gh_permissions.apps import CANONICAL_BACKEND
from gh_permissions.models import Repository
from gh_permissions.policy import register_direct, register_team
from tests.fixtures import GhFixtureMixin
from trusts.apps import implementation_for_path
from trusts.query import AuthorizedQuerySet


ROOT = Path(__file__).resolve().parents[1]


class UserFacingReadmeAndPackageTest(SimpleTestCase):
    def test_license_notice_is_beedesk_2026(self):
        text = (ROOT / 'LICENSE').read_text()
        self.assertIn('Copyright (c) 2026, BeeDesk, Inc.', text)
        self.assertNotIn('and contributors', text.split('THIS SOFTWARE')[0])
        pyproject = (ROOT / 'pyproject.toml').read_text()
        self.assertIn('license = "BSD-2-Clause"', pyproject)
        self.assertIn('readme = "README.md"', pyproject)
        self.assertNotIn('readme = "DEV.md"', pyproject)
        self.assertIn('version = "0.1.0.dev0"', pyproject)
        self.assertIn('django-trusts>=1.0.0.dev3,<2', pyproject)
        pin = (ROOT / 'scripts' / 'django-trusts.pin').read_text()
        self.assertIn('1e19b5d464c067186aada58943c3ee67c44b2aa0', pin)

    def test_user_readme_is_not_internal_status(self):
        readme = (ROOT / 'README.md').read_text()
        forbidden = (
            'Step IIb',
            'Step I',
            'Step III',
            'C1',
            'C2',
            'Z1',
            'pair pin',
            'baton',
            'code-budget',
            'kernel_config',
            'trusts.core_backends',
            '39f1f961',
            '1e19b5d',
            '1.0.0.dev2',
            '1.0.0.dev3',
            'django-trusts contributors',
        )
        offenders = [needle for needle in forbidden if needle in readme]
        self.assertEqual(offenders, [])
        self.assertIn('bounded reference', readme.lower())
        self.assertIn('not affiliated with GitHub', readme)
        self.assertIn("Do **not** list `'trusts'` in", readme)
        self.assertIn("'gh_permissions.apps.GhPermissionsConfig'", readme)
        self.assertIn("'gh_permissions.backends.GhAuthorizationBackend'", readme)
        self.assertIn('register_team', readme)
        self.assertIn('register_direct', readme)
        self.assertIn('user=t.team.members', readme)
        self.assertIn('permission_in(t.team.permission_bundles.operations)', readme)
        self.assertIn('registry.has_permission(account, repository, operation)', readme)
        self.assertIn('Repository.objects.authorized(account, operation)', readme)
        self.assertIn('BeeDesk, Inc., 2026', readme)
        self.assertIn('DEV.md', readme)
        self.assertIn('django-trusts', readme)
        self.assertIn('1.x', readme)
        settings_text = (ROOT / 'tests' / 'settings.py').read_text()
        self.assertIn("'gh_permissions.apps.GhPermissionsConfig'", settings_text)
        self.assertIn("'gh_permissions.backends.GhAuthorizationBackend'", settings_text)
        self.assertNotIn("'trusts'", settings_text)
        dev = (ROOT / 'DEV.md').read_text()
        self.assertIn('internal', dev[:800].lower())
        self.assertIn('README.md', dev[:800])


class ReadmeAuthorizationTest(GhFixtureMixin, TestCase):
    def test_documented_object_and_listing_share_compiled_policy(self):
        self.assertTrue(callable(register_direct))
        self.assertTrue(callable(register_team))
        registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
            CANONICAL_BACKEND,
        ).registry
        self.assertTrue(
            registry.has_permission(self.member, self.repo_a, self.read),
        )
        self.assertFalse(
            registry.has_permission(self.member, self.repo_a, self.write),
        )
        qs = Repository.objects.authorized(self.member, self.read)
        self.assertIsInstance(qs, AuthorizedQuerySet)
        self.assertEqual(set(qs.values_list('pk', flat=True)), {self.repo_a.pk})
        self.assertFalse(
            Repository.objects.authorized(self.stranger, self.read).exists()
        )
