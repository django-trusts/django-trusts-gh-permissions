"""GH implementation owner.

``ready`` records relations on ``self.recorder``. ``TrustsConfigMixin.ready``
then freezes that recorder and builds this app's catalog. The backend
reads the catalog from the app registry. Core is a library, not an
installed app.
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
    """GH implementation. Owns the recorder and the catalog built from it.

    ``ready()`` refuses to start when ``CANONICAL_BACKEND`` is not listed
    in ``AUTHENTICATION_BACKENDS``. A second call re-checks that path and
    does not record again.
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
        if self.recorder.frozen:
            return

        # policy and services import model modules. This AppConfig module
        # is imported while the app registry is still populating.
        from gh_permissions.policy import (
            register_all_personal_repositories_delegation,
            register_collaborator,
            register_delegation,
            register_issue_permissions,
            register_organization_owner,
            register_personal_delegation,
            register_team,
        )
        from gh_permissions.services import seed_owner_group_on_migrate

        post_migrate.connect(
            seed_owner_group_on_migrate,
            sender=self,
            dispatch_uid='gh_permissions.seed_owner_group',
        )
        register_collaborator(self.recorder)
        register_team(self.recorder)
        register_organization_owner(self.recorder)
        register_personal_delegation(self.recorder)
        register_all_personal_repositories_delegation(self.recorder)
        register_delegation(self.recorder)
        register_issue_permissions(self.recorder)
        super().ready()
