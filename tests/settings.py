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

# Session authentication for the runnable repository API. DRF is an
# example/test dependency; gh_permissions does not import it.
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework.authentication.SessionAuthentication',
    ),
}
