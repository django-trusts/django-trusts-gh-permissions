import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parents[1]

# The committed trusts-policy.lock.yaml is the MySQL 8.4 deployment
# rendering. SQLite runs set TRUSTS_POLICY_LOCKFILE to their own
# rendering so trusts.E009 and the byte comparison still apply there.
_lock_override = os.environ.get('TRUSTS_POLICY_LOCKFILE')
if _lock_override:
    TRUSTS_POLICY_LOCKFILE = Path(_lock_override)
else:
    TRUSTS_POLICY_LOCKFILE = BASE_DIR / 'trusts-policy.lock.yaml'

SECRET_KEY = 'gh-permissions-tests-not-for-production'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
ALLOWED_HOSTS = ['testserver', 'localhost']

# Relations are registered at the end of gh_permissions/models.py.
# GhPermissionsConfig owns the catalog. Core is a library.
# Do not list 'trusts'. example.User is the test project only.
# The library does not require it.
# Sessions, messages, static files, and admin are the harness for real
# organization-owner admin requests. They are not authorization.
INSTALLED_APPS = (
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'example.apps.ExampleConfig',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.admin',
    'rest_framework',
    'gh_permissions.apps.GhPermissionsConfig',
)

MIDDLEWARE = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
)

TEMPLATES = (
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': (),
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': (
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ),
        },
    },
)

STATIC_URL = '/static/'

AUTH_USER_MODEL = 'example.User'

# Two layers. GhAuthorizationBackend answers object and organization
# checks and is empty when no object is passed. ModelBackend
# authenticates passwords and supplies the no-object Django model
# permissions stock admin needs before it will open a changelist.
# ModelBackend returns nothing when an object is passed, so a model
# permission is not organization authority.
AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
    'django.contrib.auth.backends.ModelBackend',
)

# Empty on purpose. The 0002 upgrade test migrates it itself.
# The runner must not apply 0002 here, because that migration
# refuses to run backwards.
_GH_PERMISSION_RESET = {
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': ':memory:',
    'TEST': {'MIGRATE': False},
}

if os.environ.get('TRUSTS_TEST_DATABASE') == 'mysql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': os.environ.get('TRUSTS_MYSQL_NAME', 'trusts'),
            'USER': os.environ.get('TRUSTS_MYSQL_USER', 'root'),
            'PASSWORD': os.environ.get('TRUSTS_MYSQL_PASSWORD', ''),
            'HOST': os.environ.get('TRUSTS_MYSQL_HOST', '127.0.0.1'),
            'PORT': os.environ.get('TRUSTS_MYSQL_PORT', '3306'),
            'OPTIONS': {'charset': 'utf8mb4'},
        },
        'gh_permission_reset': _GH_PERMISSION_RESET,
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': ':memory:',
        },
        'gh_permission_reset': _GH_PERMISSION_RESET,
    }

ROOT_URLCONF = 'tests.urls'

# Development deployment harness. Not production token infrastructure.
# The mapping is token to username. Values come from the environment
# when this module loads. An unset or empty variable adds no entry.
# Generate a value with secrets.token_urlsafe(32). A shorter value is
# a configuration error. The same token in two variables is also a
# configuration error. Nothing here is a token value. This harness has
# no expiry, no rotation protocol, no per-token scope, no durable
# revocation or audit record, and no protection against username reuse.
# 43 is len(secrets.token_urlsafe(32)).
EXAMPLE_API_TOKEN_MIN_LENGTH = 43
_EXAMPLE_API_TOKEN_USERS = (
    ('EXAMPLE_API_TOKEN_OWNER', 'example-owner'),
    ('EXAMPLE_API_TOKEN_DIRECT', 'example-direct'),
    ('EXAMPLE_API_TOKEN_OUTSIDER', 'example-outsider'),
)


def _example_api_tokens():
    tokens = {}
    for env_name, username in _EXAMPLE_API_TOKEN_USERS:
        value = os.environ.get(env_name) or ''
        if not value:
            continue
        if len(value) < EXAMPLE_API_TOKEN_MIN_LENGTH:
            raise ImproperlyConfigured(
                '%s must be at least %s characters. '
                'Generate one with secrets.token_urlsafe(32).'
                % (env_name, EXAMPLE_API_TOKEN_MIN_LENGTH),
            )
        if value in tokens:
            raise ImproperlyConfigured(
                '%s and another EXAMPLE_API_TOKEN_* variable share one token.'
                % env_name,
            )
        tokens[value] = username
    return tokens


EXAMPLE_API_TOKENS = _example_api_tokens()

# Bearer, then session. Bearer is first so a missing or invalid token
# is DRF's 401 with WWW-Authenticate: Bearer. A request with no
# Authorization header still falls through to the session. DRF is an
# example/test dependency; gh_permissions does not import it.
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'example.authentication.BearerTokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
}
