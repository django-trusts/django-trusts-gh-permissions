#!/usr/bin/env python3
"""Fresh GH IIb install on Step I core: no kernel AppConfig, identity, --check."""

from __future__ import annotations

import os
import sys
import tempfile
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.pop('DJANGO_SETTINGS_MODULE', None)


def _put_kernel_first():
    kernel = Path(
        os.environ.get('KERNEL_CHECKOUT', ROOT / '.deps' / 'django-trusts')
    ).resolve()
    root = ROOT.resolve()
    cleaned = []
    for p in sys.path:
        abs_p = Path(p or os.getcwd()).resolve()
        if abs_p in {root, kernel}:
            continue
        cleaned.append(p)
    sys.path[:] = cleaned
    if kernel.is_dir():
        sys.path.insert(0, str(kernel))
    sys.path.append(str(root))


_put_kernel_first()

GH_TABLES = (
    'gh_permissions_account',
    'gh_permissions_organization',
    'gh_permissions_team',
    'gh_permissions_operation',
    'gh_permissions_permissionbundle',
    'gh_permissions_repository',
    'gh_permissions_accountrepogrant',
    'gh_permissions_teamrepogrant',
)

CONTENT_TYPE_KEYS = (
    ('gh_permissions', 'account'),
    ('gh_permissions', 'organization'),
    ('gh_permissions', 'team'),
    ('gh_permissions', 'operation'),
    ('gh_permissions', 'permissionbundle'),
    ('gh_permissions', 'repository'),
    ('gh_permissions', 'accountrepogrant'),
    ('gh_permissions', 'teamrepogrant'),
)

CANONICAL_BACKEND_PATH = 'gh_permissions.backends.GhAuthorizationBackend'


def configure(db_path: Path) -> None:
    from django.conf import settings

    if settings.configured:
        raise SystemExit('Django already configured')
    settings.configure(
        SECRET_KEY='gh-fresh-install',
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
        DATABASES={
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': str(db_path),
            }
        },
    )


def _applied_gh(connection) -> set[str]:
    from django.db.migrations.recorder import MigrationRecorder

    recorder = MigrationRecorder(connection)
    return {
        name for app, name in recorder.applied_migrations()
        if app == 'gh_permissions'
    }


def _gh_plan(connection):
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [
        (migration.app_label, migration.name, backwards)
        for migration, backwards in plan
        if migration.app_label == 'gh_permissions'
    ]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix='django-trusts-gh-fresh-') as tmp:
        db_path = Path(tmp) / 'fresh.sqlite3'
        configure(db_path)

        import django
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.db import connection

        django.setup()
        call_command('migrate', verbosity=0, interactive=False)

        applied = _applied_gh(connection)
        if applied != {'0001_initial'}:
            raise SystemExit('fresh applied set %s' % applied)
        if _gh_plan(connection):
            raise SystemExit('fresh install still has pending GH migrations: %s' % (
                _gh_plan(connection),
            ))

        tables = set(connection.introspection.table_names())
        missing = [name for name in GH_TABLES if name not in tables]
        if missing:
            raise SystemExit('missing tables: %s' % missing)

        from django.apps import apps as django_apps
        from django.contrib.contenttypes.models import ContentType
        from gh_permissions.apps import GhPermissionsConfig, gh_config
        from gh_permissions.models import (
            Account, AccountRepoGrant, Organization, Repository, TeamRepoGrant,
        )
        from trusts.apps import AppConfig as KernelAppConfig
        from trusts.apps import implementation_configs, kernel_config

        config = django_apps.get_app_config('gh_permissions')
        if type(config) is not GhPermissionsConfig:
            raise SystemExit('get_app_config("gh_permissions") is not GhPermissionsConfig')
        if config.name != 'gh_permissions' or config.label != 'gh_permissions':
            raise SystemExit('GhPermissionsConfig identity drifted')
        owners = implementation_configs()
        if owners != (gh_config(),) or len(owners) != 1:
            raise SystemExit('expected exactly one implementation owner, got %r' % (owners,))
        kernelish = [
            row for row in django_apps.get_app_configs()
            if type(row) is KernelAppConfig
        ]
        if kernelish:
            raise SystemExit('core AppConfig is installed: %r' % (kernelish,))
        try:
            kernel_config()
        except LookupError:
            pass
        else:
            raise SystemExit('kernel_config() succeeded without a core AppConfig')

        org = Organization.objects.create(name='fresh-org')
        account = Account.objects.create(name='fresh-account')
        repo = Repository.objects.create(organization=org, title='fresh-repo')
        if Organization.objects.get(pk=org.pk).name != 'fresh-org':
            raise SystemExit('representative organization identity drifted')
        if Repository.objects.get(pk=repo.pk).title != 'fresh-repo':
            raise SystemExit('representative repository identity drifted')

        found = {
            (ct.app_label, ct.model)
            for ct in ContentType.objects.filter(app_label='gh_permissions')
        }
        missing_ct = set(CONTENT_TYPE_KEYS) - found
        if missing_ct:
            raise SystemExit('missing content types: %s' % missing_ct)

        sql_0001 = StringIO()
        call_command('sqlmigrate', 'gh_permissions', '0001_initial', stdout=sql_0001)
        sql1 = sql_0001.getvalue()
        for table in GH_TABLES:
            if table not in sql1:
                raise SystemExit('sqlmigrate 0001 missing %s' % table)

        try:
            call_command('makemigrations', 'gh_permissions', check=True, verbosity=1)
        except CommandError as exc:
            raise SystemExit('makemigrations --check failed: %s' % exc)

        if _gh_plan(connection):
            raise SystemExit('already-current plan not empty')

        registry = gh_config().configured_backend(CANONICAL_BACKEND_PATH).registry
        roots = [record.root for record in registry.records]
        if roots != [AccountRepoGrant, TeamRepoGrant]:
            raise SystemExit('startup roots drifted: %r' % roots)

        print('fresh install ok')
        print('django', django.get_version())
        print('applied', sorted(applied))
        print('tables', ' '.join(GH_TABLES))
        print('account', account.pk, account.name)
        print('repository', repo.pk, repo.title)
        print('makemigrations --check quiet')
        print('already-current migrate --plan empty')
        print('no core AppConfig; one GhPermissionsConfig owner')
        return 0


if __name__ == '__main__':
    os.chdir(ROOT)
    raise SystemExit(main())
