import os

from django.core.exceptions import ImproperlyConfigured


SECRET_KEY = 'gh-permissions-tests-not-for-production'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
ALLOWED_HOSTS = ['testserver', 'localhost']

# IIb: core is a library, not an installed app. Zero stays absent.
# example.User is the test project only. The library does not require it.
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
    # Empty on purpose. The 0002 upgrade test migrates it itself.
    # The runner must not apply 0002 here, because that migration
    # refuses to run backwards.
    'gh_permission_reset': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {'MIGRATE': False},
    },
}

ROOT_URLCONF = 'tests.urls'

# Bearer tokens for the runnable example. The mapping is token to
# username. Values come from the environment when this module loads.
# An unset or empty variable adds no entry. The same token in two
# variables is a configuration error. Nothing here is a token value.
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
