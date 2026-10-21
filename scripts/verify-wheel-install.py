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
            'gh_permissions.apps.GhPermissionsConfig',
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
    from gh_permissions.apps import GhPermissionsConfig, gh_config
    # After django.setup(): TrustsConfigMixin is imported with the installed
    # GH app config. This script configures settings only in this function,
    # so the import cannot run at module import time.
    from trusts.apps import TrustsConfigMixin
    from gh_permissions.models import (
        AllPersonalRepositoriesDelegation,
        OrganizationOwnership,
        PersonalRepositoryDelegation,
        Repository,
        RepositoryCollaborator,
        RepositoryDelegation,
        IssuePermissions,
        TeamRepositoryPermission,
    )
    # After django.setup(): the backend reads its catalog from the
    # containing app config, which exists only once apps are ready.
    from gh_permissions.backends import GhAuthorizationBackend

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

    if 'trusts_core' in django_apps.app_configs:
        raise SystemExit('core AppConfig is installed')
    names = {config.name for config in django_apps.get_app_configs()}
    if 'trusts' in names or 'trusts.apps' in names:
        raise SystemExit('a trusts Django app is installed')

    owner = gh_config()
    if not isinstance(owner, GhPermissionsConfig):
        raise SystemExit('gh_permissions is not GhPermissionsConfig')
    if not isinstance(owner, TrustsConfigMixin):
        raise SystemExit('GhPermissionsConfig does not own a Trusts catalog')

    catalog = GhAuthorizationBackend().get_catalog()
    if catalog is not owner.get_catalog() or catalog.path != owner.catalog_path():
        raise SystemExit(
            'GH catalog was not built for %s' % owner.catalog_path()
        )
    roots = [record.root for record in catalog.records]
    if roots != [
        RepositoryCollaborator,
        TeamRepositoryPermission,
        OrganizationOwnership,
        OrganizationOwnership,
        IssuePermissions,
    ]:
        raise SystemExit(
            'GH-only populate must register collaborator, team, owner, '
            'and issue-triage roots: %r' % roots
        )
    delegation_roots = [record.root for record in catalog.delegations]
    if delegation_roots != [
        PersonalRepositoryDelegation,
        AllPersonalRepositoriesDelegation,
        RepositoryDelegation,
    ]:
        raise SystemExit(
            'GH-only populate must register personal and organization '
            'delegation roots: %r' % delegation_roots
        )

    print('wheel import ok')
    print('django', django.get_version())
    print('gh_permissions.__file__', gh_file)
    print('RepositoryCollaborator', RepositoryCollaborator)
    print('Repository', Repository)
    print('catalog', catalog.path, catalog.family)
    print('startup roots', [root.__name__ for root in roots])
    print(
        'delegation roots',
        [root.__name__ for root in delegation_roots],
    )
    print('absent zero modules', ' '.join(ABSENT_ZERO_MODULES))
    return 0


if __name__ == '__main__':
    sys.exit(main())
