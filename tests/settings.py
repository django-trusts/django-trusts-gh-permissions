SECRET_KEY = 'gh-permissions-tests-not-for-production'
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'
ALLOWED_HOSTS = ['testserver', 'localhost']

# GH-only Trusts install: kernel + this consumer. Django contrib is the
# populate minimum (auth Permission class body on the mixin). No Zero.
INSTALLED_APPS = (
    'django.contrib.contenttypes',
    'django.contrib.auth',
    'trusts',
    'gh_permissions',
)

AUTHENTICATION_BACKENDS = (
    'gh_permissions.backends.GhAuthorizationBackend',
)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

ROOT_URLCONF = 'tests.urls'
