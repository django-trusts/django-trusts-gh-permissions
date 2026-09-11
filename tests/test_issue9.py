"""#9: user-facing README and package metadata for GH on final core.

Executable README spellings are the verified test settings, the
accepted team ``Ref`` registration, the owner/registry object call,
and the two-checkout install sequence.
"""

import sys
import types
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, TestCase

from gh_permissions.apps import (
    CANONICAL_BACKEND,
    CORE_REQUIREMENT,
    FLOOR_MESSAGE,
    _load_implementation_config,
)
from gh_permissions.models import Repository, TeamRepoGrant
from gh_permissions.policy import register_direct, register_team
from tests.fixtures import GhFixtureMixin
from trusts.apps import implementation_for_path
from trusts.core import All, Equal, PermissionIn


ROOT = Path(__file__).resolve().parents[1]


FORBIDDEN_README = (
    'Step IIb',
    'Step I',
    'Step II',
    'C1',
    'C2',
    'Z1',
    'pair pin',
    'baton',
    'code-budget',
    'code budget',
    'kernel_config',
    'trusts.core_backends',
    '1.0.0.dev2',
    '1.0.0.dev3',
    '39f1f961',
    '1e19b5d',
    '11058641',
    'a071415',
    'pair CI',
    'CI topology',
    'GitHub Actions',
    'migrates.md',
    'register_gh_policy',
    "from trusts.backends import TrustModelBackend\n",
)


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
        self.assertIn('"django-trusts>=1.0.0.dev3,<2"', pyproject)

    def test_user_readme_is_not_internal_status(self):
        readme = (ROOT / 'README.md').read_text()
        offenders = [needle for needle in FORBIDDEN_README if needle in readme]
        self.assertEqual(offenders, [])
        self.assertNotIn("'trusts',", readme)
        self.assertIn("Do **not** list `'trusts'` in `INSTALLED_APPS`", readme)
        self.assertIn('bounded reference implementation', readme)
        self.assertIn('not affiliated with GitHub', readme)
        self.assertIn('not a migration target', readme.lower())
        self.assertIn('django-trusts', readme)
        self.assertIn('1.x', readme)
        self.assertIn("'gh_permissions.apps.GhPermissionsConfig'", readme)
        self.assertIn("'gh_permissions.backends.GhAuthorizationBackend'", readme)
        self.assertIn('from trusts.core import All, Equal, Ref, permission_in', readme)
        self.assertIn('t = Ref(TeamRepoGrant)', readme)
        self.assertIn('user=t.team.members', readme)
        self.assertIn('permission_in(t.team.permission_bundles.operations)', readme)
        self.assertIn('Equal(t.team.organization, t.repository.organization)', readme)
        self.assertIn('AccountRepoGrant', readme)
        self.assertIn('from gh_permissions.apps import CANONICAL_BACKEND', readme)
        self.assertIn('from trusts.apps import implementation_for_path', readme)
        self.assertIn(
            'registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(',
            readme,
        )
        self.assertIn(
            'registry.has_permission(account, repository, operation)',
            readme,
        )
        self.assertIn('Repository.objects.authorized(account, operation)', readme)
        self.assertNotIn('.exists()', readme)
        self.assertIn(
            'python -m pip install "Django>=6.1,<6.2"\n'
            'python -m pip install ../django-trusts\n'
            'python -m pip install .',
            readme,
        )
        self.assertNotIn('git+https://', readme)
        self.assertNotIn('@', readme.split('Copyright')[0])
        self.assertIn('BeeDesk, Inc., 2026 (BSD-2-Clause)', readme)
        self.assertIn('DEV.md', readme)
        self.assertIn('no implicit permission-level', readme.lower())
        self.assertIn('org-owner admin', readme)
        self.assertIn('public anonymous read', readme)
        self.assertIn('nested teams', readme)
        self.assertIn('CODEOWNERS', readme)
        dev = (ROOT / 'DEV.md').read_text()
        self.assertIn('internal', dev[:800].lower())
        self.assertIn('development', dev[:800].lower())
        self.assertIn('README.md', dev[:800])
        self.assertIn('superseded', dev[:800].lower())

    def test_readme_examples_match_verified_settings_and_policy(self):
        readme = (ROOT / 'README.md').read_text()
        settings_text = (ROOT / 'tests' / 'settings.py').read_text()
        self.assertIn("'gh_permissions.apps.GhPermissionsConfig'", settings_text)
        self.assertIn(
            "'gh_permissions.backends.GhAuthorizationBackend'",
            settings_text,
        )
        self.assertNotIn("'trusts',", settings_text)
        self.assertIn(
            "INSTALLED_APPS = (\n"
            "    'django.contrib.contenttypes',\n"
            "    'django.contrib.auth',\n"
            "    'gh_permissions.apps.GhPermissionsConfig',\n"
            ")",
            readme,
        )
        self.assertIn(
            "AUTHENTICATION_BACKENDS = (\n"
            "    'gh_permissions.backends.GhAuthorizationBackend',\n"
            ")",
            readme,
        )
        policy = (ROOT / 'gh_permissions' / 'policy.py').read_text()
        self.assertIn('def register_team(registry):', policy)
        self.assertIn('content=t.repository', policy)
        self.assertIn('user=t.team.members', policy)
        self.assertIn('permission=t.operation', policy)
        self.assertIn(
            'permission_in(t.team.permission_bundles.operations)',
            policy,
        )
        self.assertIn(
            'Equal(t.team.organization, t.repository.organization)',
            policy,
        )
        self.assertIn(register_direct.__name__, policy)
        self.assertIn(register_team.__name__, policy)
        self.assertFalse(hasattr(
            __import__('gh_permissions.policy', fromlist=['register_gh_policy']),
            'register_gh_policy',
        ))
        ci = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text()
        self.assertIn('python -m pip install -e .deps/django-trusts', ci)
        self.assertIn('python -m pip install -e .', ci)
        self.assertLess(
            ci.index('python -m pip install -e .deps/django-trusts'),
            ci.index('python -m pip install -e . --config-settings editable_mode=compat'),
        )


class FloorMessageTest(SimpleTestCase):
    def test_floor_message_names_final_core(self):
        source = (ROOT / 'gh_permissions' / 'apps.py').read_text()
        self.assertEqual(CORE_REQUIREMENT, 'django-trusts>=1.0.0.dev3,<2')
        self.assertIn(CORE_REQUIREMENT, FLOOR_MESSAGE)
        self.assertIn('TrustsImplementationConfig', FLOOR_MESSAGE)
        self.assertIn('raise ImproperlyConfigured(FLOOR_MESSAGE)', source)
        self.assertNotIn('1.0.0.dev2', source)
        self.assertNotIn('1.0.0.dev2', FLOOR_MESSAGE)

    def test_missing_implementation_config_raises_floor_message(self):
        fake = types.ModuleType('trusts.apps')
        with patch.dict(sys.modules, {'trusts.apps': fake}):
            with self.assertRaises(ImproperlyConfigured) as ctx:
                _load_implementation_config()
        self.assertEqual(str(ctx.exception), FLOOR_MESSAGE)
        self.assertIn('django-trusts>=1.0.0.dev3,<2', str(ctx.exception))


class ReadmeExampleAuthorizationTest(GhFixtureMixin, TestCase):
    def test_documented_settings_are_the_live_test_settings(self):
        self.assertEqual(
            settings.INSTALLED_APPS,
            (
                'django.contrib.contenttypes',
                'django.contrib.auth',
                'gh_permissions.apps.GhPermissionsConfig',
            ),
        )
        self.assertEqual(
            settings.AUTHENTICATION_BACKENDS,
            ('gh_permissions.backends.GhAuthorizationBackend',),
        )
        self.assertNotIn('trusts', settings.INSTALLED_APPS)

    def test_documented_team_ref_matches_startup_registration(self):
        owner = implementation_for_path(CANONICAL_BACKEND)
        record = owner.configured_backend(CANONICAL_BACKEND).registry.records[1]
        self.assertIs(record.root, TeamRepoGrant)
        self.assertEqual(record.user_path, ('team', 'members'))
        self.assertIsInstance(record.condition, All)
        self.assertIsInstance(record.condition.predicates[0], PermissionIn)
        self.assertIsInstance(record.condition.predicates[1], Equal)

    def test_documented_object_and_listing_share_compiled_policy(self):
        registry = implementation_for_path(CANONICAL_BACKEND).configured_backend(
            CANONICAL_BACKEND,
        ).registry
        self.assertTrue(
            registry.has_permission(self.member, self.repo_a, self.read),
        )
        self.assertEqual(
            list(
                Repository.objects.authorized(self.member, self.read)
                .order_by('pk')
            ),
            [self.repo_a],
        )
        self.assertTrue(
            registry.has_permission(
                self.collaborator, self.repo_b, self.write,
            ),
        )
        self.assertEqual(
            list(
                Repository.objects.authorized(self.collaborator, self.write)
                .order_by('pk')
            ),
            [self.repo_b],
        )
        self.assertFalse(
            registry.has_permission(self.member, self.repo_b, self.read),
        )
