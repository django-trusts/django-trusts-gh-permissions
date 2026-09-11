SECRET_KEY = 'gh-permissions-tests-not-for-production'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
LOGIN_URL = '/accounts/login/'
STATIC_URL = '/static/'
ALLOWED_HOSTS = ['testserver', 'localhost']

INSTALLED_APPS = (
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'trusts',
    'gh_permissions.apps.GhPermissionsConfig',
)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

ROOT_URLCONF = 'tests.urls'
