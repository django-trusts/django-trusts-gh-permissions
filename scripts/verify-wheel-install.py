#!/usr/bin/env python3
"""Import-smoke the installed django-trusts-gh-permissions wheel.

Must not run with the repository root as cwd or on sys.path. A passing
result means GH modules loaded from the installed distribution after
Django setup, ``trusts.zero`` is absent, and the IIb owner donated both
roots without a core AppConfig.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


ABSENT_ZERO_MODULES = [
    'trusts.zero',
    'trusts.zero.models',
    'trusts.zero.backends',
]
CANONICAL_BACKEND_PATH = 'gh_permissions.backends.GhAuthorizationBackend'


def find_shipped_modules(names):
    shipped = []
    for name in names:
        try:
            spec = importlib.util.find_spec(name)
        except ModuleNotFoundError:
            continue
        if spec is not None:
            shipped.append(name)
    return shipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--checkout',
        required=True,
        help='Absolute path to the repository checkout that must not be imported',
    )
    args = parser.parse_args()
    checkout = Path(args.checkout).resolve()
    cwd = Path.cwd().resolve()

    if cwd == checkout or checkout in cwd.parents:
        raise SystemExit(
            'Refuse to run from the checkout (%s). cd to a temporary directory.' % cwd
        )

    leaked = []
    for entry in sys.path:
        if entry == '':
            if cwd == checkout:
                leaked.append("'' (cwd is the checkout)")
            continue
        try:
            resolved = Path(entry).resolve()
        except OSError:
            continue
        if resolved == checkout:
            leaked.append(entry)
    if leaked:
        raise SystemExit('Checkout leaked onto sys.path: %s' % leaked)

    from django.conf import settings

    settings.configure(
        SECRET_KEY='wheel-import-smoke',
        USE_TZ=True,
        DEFAULT_AUTO_FIELD='django.db.models.AutoField',
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
            'gh_permissions',
        ],
        AUTHENTICATION_BACKENDS=[
            CANONICAL_BACKEND_PATH,
        ],
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
    )

    import django
    django.setup()

    import sys as _sys
    import gh_permissions
    import gh_permissions.models
    import gh_permissions.policy
    from django.apps import apps as django_apps
    from gh_permissions.apps import (
        CANONICAL_BACKEND_PATH as owned_path,
        GhPermissionsConfig,
        gh_config,
    )
    from gh_permissions.models import (
        Account,
        AccountRepoGrant,
        Repository,
        TeamRepoGrant,
    )
    from trusts.apps import (
        AppConfig as KernelAppConfig,
        implementation_configs,
        implementation_for_path,
        kernel_config,
    )

    gh_file = Path(gh_permissions.__file__).resolve()
    if checkout == gh_file or checkout in gh_file.parents:
        raise SystemExit('Imported gh_permissions from the checkout: %s' % gh_file)
    if 'site-packages' not in str(gh_file) and 'dist-packages' not in str(gh_file):
        raise SystemExit(
            'gh_permissions.__file__ is not a site-packages install: %s' % gh_file
        )

    leaked_zero = find_shipped_modules(ABSENT_ZERO_MODULES)
    if leaked_zero:
        raise SystemExit('GH wheel install must not ship Zero modules: %s' % leaked_zero)
    if 'trusts.zero' in _sys.modules:
        raise SystemExit('GH wheel populate imported trusts.zero')

    owner = gh_config()
    if type(owner) is not GhPermissionsConfig:
        raise SystemExit('gh_config() is not GhPermissionsConfig: %r' % (owner,))
    if owner.label != 'gh_permissions' or owner.name != 'gh_permissions':
        raise SystemExit('GH name/label drifted: %r/%r' % (owner.name, owner.label))
    owners = implementation_configs()
    if owners != (owner,) or len(owners) != 1:
        raise SystemExit('expected exactly one implementation owner, got %r' % (owners,))
    if implementation_for_path(owned_path) is not owner:
        raise SystemExit('canonical path does not resolve to GhPermissionsConfig')
    kernelish = [
        config for config in django_apps.get_app_configs()
        if type(config) is KernelAppConfig
    ]
    if kernelish:
        raise SystemExit('core AppConfig is installed: %r' % (kernelish,))
    try:
        kernel_config()
    except LookupError:
        pass
    else:
        raise SystemExit('kernel_config() succeeded without a core AppConfig')

    registry = owner.configured_backend(CANONICAL_BACKEND_PATH).registry
    roots = [record.root for record in registry.records]
    if roots != [AccountRepoGrant, TeamRepoGrant]:
        raise SystemExit(
            'GH-only populate must register direct and team roots: %r' % roots
        )

    print('wheel import ok')
    print('django', django.get_version())
    print('gh_permissions.__file__', gh_file)
    print('Account', Account)
    print('Repository', Repository)
    print('owner', owner, owner.label)
    print('startup roots', [root.__name__ for root in roots])
    print('absent zero modules', ' '.join(ABSENT_ZERO_MODULES))
    print('no core AppConfig; one GhPermissionsConfig owner')
    return 0


if __name__ == '__main__':
    sys.exit(main())
