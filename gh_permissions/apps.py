"""GH implementation owner.

Relations are registered at the end of ``models.py`` through
``GhAuthorizationBackend().get_registry()``. ``ready`` checks the
canonical backend and connects the owner-group seed. The catalog is
built on the first policy check or permission question. Core is a
library, not an installed app.
"""

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models.signals import post_migrate

from trusts.apps import TrustsConfigMixin


CANONICAL_BACKEND = 'gh_permissions.backends.GhAuthorizationBackend'


def gh_config(apps_registry=None):
    """Return the installed ``GhPermissionsConfig`` by class identity."""
    registry = django_apps if apps_registry is None else apps_registry
    matches = [
        config for config in registry.get_app_configs()
        if type(config) is GhPermissionsConfig
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ImproperlyConfigured(
            'No installed gh_permissions.apps.GhPermissionsConfig.'
        )
    raise ImproperlyConfigured(
        'Multiple GhPermissionsConfig instances: %r' % (matches,)
    )


class GhPermissionsConfig(TrustsConfigMixin):
    """GH implementation. The catalog is built from ``models.py``.

    ``ready()`` refuses to start when ``CANONICAL_BACKEND`` is not listed
    in ``AUTHENTICATION_BACKENDS``.
    """

    name = 'gh_permissions'
    label = 'gh_permissions'
    verbose_name = 'GH permissions'
    default_auto_field = 'django.db.models.AutoField'
    default = True

    def ready(self):
        if CANONICAL_BACKEND not in tuple(settings.AUTHENTICATION_BACKENDS or ()):
            raise ImproperlyConfigured(
                'AUTHENTICATION_BACKENDS must include %s.'
                % CANONICAL_BACKEND
            )

        # services imports model modules. This AppConfig module is
        # imported while the app registry is still populating.
        from gh_permissions.services import seed_owner_group_on_migrate

        post_migrate.connect(
            seed_owner_group_on_migrate,
            sender=self,
            dispatch_uid='gh_permissions.seed_owner_group',
        )
        super().ready()
