import os
import sys

os.environ['DJANGO_SETTINGS_MODULE'] = 'tests.settings'
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

import django
from django.conf import settings
from django.test.utils import get_runner

SUITE = [
    'tests.test_naming',
    'tests.test_kernel',
    'tests.test_acceptance',
    'tests.test_fail_closed',
    'tests.test_team_mapping',
    'tests.test_issue6',
    'tests.test_issue9',
    'tests.test_issue20',
    'tests.test_permitted_users',
    'tests.test_migration_0002',
    'tests.test_issue27',
    'tests.test_content_type_mismatch',
    'tests.test_issue28',
    'tests.test_admin_services',
    'tests.test_org_scoped_admin',
    'tests.test_seed_example',
    'tests.test_runnable_admin',
    'tests.test_mcp_authorization',
]


def runtests():
    django.setup()
    TestRunner = get_runner(settings)
    test_runner = TestRunner(verbosity=1, interactive=False)
    failures = test_runner.run_tests(SUITE)
    sys.exit(bool(failures))


if __name__ == '__main__':
    runtests()
