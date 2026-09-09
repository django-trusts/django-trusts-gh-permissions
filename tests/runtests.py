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
    'tests.test_acceptance',
    'tests.test_fail_closed',
    'tests.test_framework_ui',
]


def runtests():
    django.setup()
    TestRunner = get_runner(settings)
    test_runner = TestRunner(verbosity=1, interactive=False)
    failures = test_runner.run_tests(SUITE)
    sys.exit(bool(failures))


if __name__ == '__main__':
    runtests()
