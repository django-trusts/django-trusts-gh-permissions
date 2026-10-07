#!/usr/bin/env python3
"""One allowed and one denied repository API request.

Uses DRF's test client and the development seed. Session login is the
authentication. Passwords stay in the seed command; this script does
not print them.

    python scripts/drf_authorization_proof.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')
    database = tempfile.NamedTemporaryFile(
        prefix='drf-proof-', suffix='.sqlite3', delete=False,
    )
    database.close()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')

    import django
    from django.conf import settings

    settings.DATABASES['default']['NAME'] = database.name
    django.setup()

    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from rest_framework.test import APIClient

    from example.management.commands.seed_example import (
        DEFAULT_ORGANIZATION_NAME,
        DEVELOPMENT_PASSWORDS,
        DIRECT_USERNAME,
        OUTSIDER_USERNAME,
        OWNER_USERNAME,
        REPOSITORY_TITLE,
    )
    from gh_permissions.models import Organization, Repository

    try:
        call_command('migrate', verbosity=0, interactive=False)
        call_command('seed_example', stdout=StringIO())
        User = get_user_model()
        organization = Organization.objects.get(name=DEFAULT_ORGANIZATION_NAME)
        repository = Repository.objects.get(
            organization=organization, name=REPOSITORY_TITLE,
        )
        client = APIClient()

        owner = User.objects.get(username=OWNER_USERNAME)
        if not client.login(
            username=owner.username,
            password=DEVELOPMENT_PASSWORDS[OWNER_USERNAME],
        ):
            raise SystemExit('Owner session login failed.')
        allowed = client.get('/api/repositories/')
        client.logout()
        print(
            'ALLOWED %s GET /api/repositories/ %s'
            % (OWNER_USERNAME, allowed.status_code)
        )
        print(allowed.content.decode())

        outsider = User.objects.get(username=OUTSIDER_USERNAME)
        if not client.login(
            username=outsider.username,
            password=DEVELOPMENT_PASSWORDS[OUTSIDER_USERNAME],
        ):
            raise SystemExit('Outsider session login failed.')
        denied = client.get('/api/repositories/%s/' % repository.pk)
        print(
            'DENIED %s GET /api/repositories/%s/ %s'
            % (OUTSIDER_USERNAME, repository.pk, denied.status_code)
        )
        print(denied.content.decode())
        if repository.name in denied.content.decode():
            raise SystemExit('Denied response leaked the repository name.')

        direct = User.objects.get(username=DIRECT_USERNAME)
        client.logout()
        if not client.login(
            username=direct.username,
            password=DEVELOPMENT_PASSWORDS[DIRECT_USERNAME],
        ):
            raise SystemExit('Direct collaborator session login failed.')
        mutation = client.post('/api/repositories/', {
            'name': 'proof-denied',
            'organization_id': organization.pk,
        }, format='json')
        print(
            'DENIED %s POST /api/repositories/ %s'
            % (DIRECT_USERNAME, mutation.status_code)
        )
        print(mutation.content.decode())
        if Repository.objects.filter(name='proof-denied').exists():
            raise SystemExit('Denied create wrote a repository.')

        if allowed.status_code != 200 or denied.status_code != 404:
            return 1
        if mutation.status_code != 403:
            return 1
        if REPOSITORY_TITLE not in allowed.content.decode():
            raise SystemExit('Allowed list did not include the seeded repository.')
        return 0
    finally:
        try:
            os.unlink(database.name)
        except OSError:
            pass


if __name__ == '__main__':
    sys.exit(main())
