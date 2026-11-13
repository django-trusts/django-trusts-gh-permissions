import os
from pathlib import Path


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
ALLOWED_HOSTS = ['testserver', 'localhost', '127.0.0.1']

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
    # Example MCP authorization spike only. Not a gh_permissions dependency.
    'oauth2_provider',
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
    # File database so `manage.py migrate`, `seed_example`, and `runserver`
    # share rows. The test runner still substitutes its own database.
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        },
        'gh_permission_reset': _GH_PERMISSION_RESET,
    }

ROOT_URLCONF = 'tests.urls'

# Stock Django login. The MCP authorize view sends an anonymous human here
# and comes back through the ``next`` parameter. This is not admin login.
LOGIN_URL = '/accounts/login/'

# django-oauth-toolkit 3.4.1 is the authorization server for this example:
# PKCE, the authorization-code grant, RFC 8414 and RFC 9728 metadata, and
# the token endpoint. The access token lasts one hour and the validator
# drops the refresh token. It is a test credential for /mcp only.
OAUTH2_PROVIDER = {
    'PKCE_REQUIRED': True,
    # RFC 9700 §2.1.1. Server-wide: 3.4.1 has no per-application PKCE
    # method. PKCE_REQUIRED may be a client_id callable, but that only
    # requires some challenge. This gate rejects "plain" when the code
    # is saved and drops it from authorization-server metadata.
    'COMPLIANT_BCP_RFC9700_PKCE_METHOD': True,
    'REQUEST_APPROVAL_PROMPT': 'force',
    'ALLOWED_REDIRECT_URI_SCHEMES': ['http', 'https'],
    'ALLOW_LOCALHOST_LOOPBACK': True,
    'OAUTH2_VALIDATOR_CLASS': (
        'example.mcp_authorization.ExampleAccessTokenValidator'
    ),
    'OAUTH2_GRANT_TYPES_SUPPORTED': ['authorization_code'],
    'OAUTH2_RESPONSE_TYPES_SUPPORTED': ['code'],
    'OAUTH2_TOKEN_ENDPOINT_AUTH_METHODS_SUPPORTED': ['none'],
    'ACCESS_TOKEN_EXPIRE_SECONDS': 3600,
    'OAUTH2_PROTECTED_RESOURCE_NAME': 'Example MCP',
    'SCOPES': {
        'read_repository': 'Read access to repository contents',
        'write_repository': 'Read and write access to repository contents',
    },
}
