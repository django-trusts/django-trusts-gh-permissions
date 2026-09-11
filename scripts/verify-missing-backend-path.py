#!/usr/bin/env python3
"""Missing canonical GH backend path must fail populate. No silent skip."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KERNEL = Path(
    os.environ.get('KERNEL_CHECKOUT', ROOT / '.deps' / 'django-trusts')
).resolve()
CANONICAL_BACKEND_PATH = 'gh_permissions.backends.GhAuthorizationBackend'

PROBE = r'''
import os, sys
from pathlib import Path
root = Path(%r).resolve()
kernel = Path(%r).resolve()
sys.path.insert(0, str(kernel))
sys.path.append(str(root))
os.environ.pop("DJANGO_SETTINGS_MODULE", None)
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
settings.configure(
    SECRET_KEY="iib-missing-path",
    USE_TZ=True,
    DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    INSTALLED_APPS=[
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "gh_permissions",
    ],
    AUTHENTICATION_BACKENDS=["django.contrib.auth.backends.ModelBackend"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
)
import django
try:
    django.setup()
except ImproperlyConfigured as exc:
    print("improperly-configured", exc)
    raise SystemExit(0)
print("populate-succeeded")
raise SystemExit(1)
'''


def main() -> int:
    result = subprocess.run(
        [sys.executable, '-c', PROBE % (str(ROOT), str(KERNEL))],
        cwd=str(ROOT),
        env={k: v for k, v in os.environ.items() if k != 'DJANGO_SETTINGS_MODULE'},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            'missing-path gate failed:\nstdout=%s\nstderr=%s'
            % (result.stdout, result.stderr)
        )
    if 'improperly-configured' not in result.stdout:
        raise SystemExit('expected ImproperlyConfigured, got: %s' % result.stdout)
    if CANONICAL_BACKEND_PATH not in result.stdout:
        raise SystemExit('error must name %s: %s' % (
            CANONICAL_BACKEND_PATH, result.stdout,
        ))
    if 'populate-succeeded' in result.stdout:
        raise SystemExit('missing path must not populate')
    print('missing backend path fails startup')
    print(result.stdout.strip())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
