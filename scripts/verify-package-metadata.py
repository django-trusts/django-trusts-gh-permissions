#!/usr/bin/env python3
"""Prove wheel/sdist identity: GH 0.1.0.dev0, core floor, BSD-2-Clause, LICENSE."""

from __future__ import annotations

import os
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = '0.1.0.dev0'
EXPECTED_NAME = 'django-trusts-gh-permissions'
EXPECTED_CORE = 'django-trusts>=1.0.0.dev3,<2'
FORBIDDEN_LONG_DESC = (
    'Step IIb',
    'Step I',
    'Step II',
    'C1',
    'C2',
    'Z1',
    'pair pin',
    'baton',
    'code-budget',
    'kernel_config',
    'trusts.core_backends',
    '1.0.0.dev2',
    '1.0.0.dev3',
    '39f1f961',
    'bc25cd95',
    '11058641',
    'a071415',
)


def _dist() -> Path:
    return ROOT / 'dist'


def _require_dist(files: list[Path], kind: str) -> Path:
    if not files:
        raise SystemExit('no %s in dist/' % kind)
    return files[-1]


def _core_floor_ok(requires: list[str]) -> bool:
    compact = [item.replace(' ', '') for item in requires]
    return any(
        'django-trusts' in item
        and '>=1.0.0.dev3' in item
        and '<2' in item
        for item in compact
    )


def _long_description(meta_text: str) -> str:
    """RFC 822 payload after the header block (the user README)."""
    parts = meta_text.split('\n\n', 1)
    if len(parts) == 2:
        return parts[1]
    return ''


def _check_metadata(meta_text: str, origin: str) -> None:
    meta = Parser().parsestr(meta_text)
    if meta.get('Name') != EXPECTED_NAME:
        raise SystemExit('%s Name is %r, expected %s' % (origin, meta.get('Name'), EXPECTED_NAME))
    if meta.get('Version') != EXPECTED_VERSION:
        raise SystemExit(
            '%s Version is %r, expected %s' % (origin, meta.get('Version'), EXPECTED_VERSION)
        )
    requires = meta.get_all('Requires-Dist') or []
    if not _core_floor_ok(requires):
        raise SystemExit('%s missing core floor %s; got %r' % (origin, EXPECTED_CORE, requires))
    license_expr = meta.get('License-Expression') or meta.get('License') or ''
    if 'BSD-2-Clause' not in license_expr:
        raise SystemExit('%s license is %r, expected BSD-2-Clause' % (origin, license_expr))
    description = meta.get('Description') or _long_description(meta_text)
    if 'bounded reference implementation' not in description:
        raise SystemExit('%s long description is not the user README' % origin)
    if "Do **not** list `'trusts'` in `INSTALLED_APPS`" not in description:
        raise SystemExit('%s long description missing INSTALLED_APPS warning' % origin)
    if 'BeeDesk, Inc., 2026 (BSD-2-Clause)' not in description:
        raise SystemExit('%s long description missing BeeDesk 2026 notice' % origin)
    if 'pip install django-trusts-gh-permissions' in description:
        raise SystemExit('%s long description still has a bare PyPI install' % origin)
    for needle in FORBIDDEN_LONG_DESC:
        if needle in description:
            raise SystemExit(
                '%s long description still contains internal-status language: %s'
                % (origin, needle)
            )


def _check_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        meta_name = next(
            (name for name in names if name.endswith('.dist-info/METADATA')),
            None,
        )
        if meta_name is None:
            raise SystemExit('wheel missing METADATA: %s' % wheel.name)
        _check_metadata(zf.read(meta_name).decode(), wheel.name)
        license_hits = [
            name for name in names
            if name.endswith('/LICENSE') or name.endswith('.dist-info/LICENSE')
        ]
        if not license_hits:
            raise SystemExit('wheel missing LICENSE: %s' % names[-20:])
        notice = zf.read(license_hits[0]).decode()
        if 'Copyright (c) 2026, BeeDesk, Inc.' not in notice:
            raise SystemExit('wheel LICENSE notice is not BeeDesk 2026')
        if 'and contributors' in notice.split('THIS SOFTWARE')[0]:
            raise SystemExit('wheel LICENSE copyright adds "and contributors"')
        if 'django_trusts_gh_permissions-0.1.0.dev0' not in wheel.name:
            raise SystemExit('wheel filename is not 0.1.0.dev0: %s' % wheel.name)
    print('wheel metadata ok', wheel.name)


def _check_sdist(sdist: Path) -> None:
    with tarfile.open(sdist, 'r:gz') as tf:
        names = tf.getnames()
        prefix = 'django_trusts_gh_permissions-0.1.0.dev0'
        if not any(name == prefix or name.startswith(prefix + '/') for name in names):
            raise SystemExit('sdist is not 0.1.0.dev0: %s' % sdist.name)
        license_name = next((name for name in names if name.endswith('/LICENSE')), None)
        if license_name is None:
            raise SystemExit('sdist missing LICENSE')
        notice = tf.extractfile(license_name).read().decode()
        if 'Copyright (c) 2026, BeeDesk, Inc.' not in notice:
            raise SystemExit('sdist LICENSE notice is not BeeDesk 2026')
        pkg_info = next((name for name in names if name.endswith('/PKG-INFO')), None)
        if pkg_info is None:
            raise SystemExit('sdist missing PKG-INFO')
        _check_metadata(tf.extractfile(pkg_info).read().decode(), sdist.name)
        readme_name = next((name for name in names if name.endswith('/README.md')), None)
        if readme_name is None:
            raise SystemExit('sdist missing README.md')
        readme = tf.extractfile(readme_name).read().decode()
        if 'bounded reference implementation' not in readme:
            raise SystemExit('sdist README.md is not the user README')
        if any(name.endswith('/DEV.md') for name in names):
            pass
        else:
            raise SystemExit('sdist missing DEV.md')
    print('sdist metadata ok', sdist.name)


def main() -> int:
    dist = _dist()
    wheels = sorted(dist.glob('django_trusts_gh_permissions-*.whl'))
    sdists = sorted(dist.glob('django_trusts_gh_permissions-*.tar.gz'))
    wheel = _require_dist(wheels, 'wheel')
    sdist = _require_dist(sdists, 'sdist')
    _check_wheel(wheel)
    _check_sdist(sdist)
    print('package metadata ok')
    print('gh', EXPECTED_NAME + '==' + EXPECTED_VERSION)
    print('requires', EXPECTED_CORE)
    print('license BSD-2-Clause')
    return 0


if __name__ == '__main__':
    os.chdir(ROOT)
    raise SystemExit(main())
