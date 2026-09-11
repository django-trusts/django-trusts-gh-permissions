#!/usr/bin/env python3
"""Import-smoke the installed django-trusts-gh-permissions wheel.

Must not run with the repository root as cwd or on sys.path. A passing
result means GH modules loaded from the installed distribution after
Django setup, and ``trusts.zero`` is absent.
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
            'trusts',
            'gh_permissions',
        ],
        AUTHENTICATION_BACKENDS=[
            'gh_permissions.backends.GhAuthorizationBackend',
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
    from gh_permissions.models import (
        Account,
        AccountRepoGrant,
        Repository,
        TeamRepoGrant,
    )
    from trusts.apps import AppConfig, kernel_config

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

    config = kernel_config()
    if type(config) is not AppConfig:
        raise SystemExit('kernel_config() is not trusts.apps.AppConfig: %r' % (config,))
    if config.label != 'trusts_core' or config.name != 'trusts':
        raise SystemExit('C2 kernel name/label must be trusts/trusts_core: %r/%r' % (
            config.name, config.label,
        ))
    if list(config.get_models()):
        raise SystemExit('C2 kernel must expose no concrete models: %r' % (
            list(config.get_models()),
        ))
    try:
        django_apps.get_app_config('trusts')
    except LookupError:
        pass
    else:
        raise SystemExit('GH-only populate must not own label trusts')

    registry = config.configured_backend().registry
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
    print('kernel_config', config, config.label)
    print('startup roots', [root.__name__ for root in roots])
    print('absent zero modules', ' '.join(ABSENT_ZERO_MODULES))
    return 0


if __name__ == '__main__':
    sys.exit(main())
