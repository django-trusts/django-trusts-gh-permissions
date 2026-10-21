"""GH app config. This app owns its Trusts recorder and catalog."""

from django.apps import apps as django_apps
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


def gh_catalog():
    """Catalog this config built in ``ready``."""
    return gh_config().get_catalog()


class GhPermissionsConfig(TrustsConfigMixin):
    """GH implementation owner. Core is a library, not an installed app.

    The backend module lives in this app, so Django's app registry
    resolves ``GhAuthorizationBackend`` to this config. ``ready``
    records each relation on ``self.recorder`` and then builds the
    catalog. It does not call ``kernel_config()`` or skip a missing
    registration.
    """

    name = 'gh_permissions'
    label = 'gh_permissions'
    verbose_name = 'GH permissions'
    default_auto_field = 'django.db.models.AutoField'
    default = True

    def ready(self):
        # Re-entry finds the recorder frozen and stops before register.
        if self.recorder.frozen:
            return
        # Phase 1 loads this module before models. policy and services
        # import models, so those imports stay in ready().
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
