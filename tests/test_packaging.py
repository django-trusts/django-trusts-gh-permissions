"""Source-tree proofs that GH IIb pins Step I and does not ship Zero."""

from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[1]
STEP_I_SHA = '39f1f9611e214193aec4e97526cf9b54ee689967'


class GhPublishMetadataTests(SimpleTestCase):
    def test_pyproject_requires_step_i_floor(self):
        text = (ROOT / 'pyproject.toml').read_text()
        self.assertIn('name = "django-trusts-gh-permissions"', text)
        self.assertIn('version = "0.1.0.dev0"', text)
        self.assertIn('"django-trusts>=1.0.0.dev2,<2"', text)
        self.assertIn('"Django>=6.1,<6.2"', text)
        self.assertNotIn('django-trusts-zero', text)

    def test_pin_and_ci_name_exact_step_i_merge(self):
        pin = (ROOT / 'scripts' / 'django-trusts.pin').read_text()
        self.assertIn(STEP_I_SHA, pin)
        req = (ROOT / 'requirements.txt').read_text()
        self.assertIn(STEP_I_SHA, req)
        ci = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text()
        self.assertIn(
            'COMPANION_KERNEL_SHA: %s' % STEP_I_SHA, ci,
        )
        self.assertNotIn('django-trusts-zero', req)

    def test_package_does_not_ship_zero_or_kernel_app(self):
        self.assertFalse((ROOT / 'trusts').exists())
        self.assertFalse((ROOT / 'trusts' / 'zero').exists())


class GhSourceLayoutTests(SimpleTestCase):
    def test_gh_python_sources_do_not_import_zero_or_kernel_accessor(self):
        banned = (
            'from trusts.apps import kernel_config',
            'from trusts.core_backends',
            'import trusts.core_backends',
            'from trusts.zero',
            'import trusts.zero',
            'django-trusts-zero',
            'def register_gh_policy',
        )
        offenders = []
        for path in (ROOT / 'gh_permissions').rglob('*.py'):
            text = path.read_text()
            for needle in banned:
                if needle in text:
                    offenders.append(
                        '%s: %s' % (path.relative_to(ROOT), needle)
                    )
        self.assertEqual(offenders, [])
