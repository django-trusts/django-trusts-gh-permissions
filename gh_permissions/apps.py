from django.apps import AppConfig
from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models.signals import post_migrate

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


class GhPermissionsConfig(AppConfig):
    """GH implementation. Registers on ``GhAuthorizationBackend`` before the sweep.

    ``trusts.apps.TrustsImplementationConfig`` is installed after this
    app and builds ``Catalog(factory.build())`` from the recorder.
    ``ready()`` refuses to start when ``CANONICAL_BACKEND`` is not listed
    in ``AUTHENTICATION_BACKENDS``.
    """

    name = 'gh_permissions'
    label = 'gh_permissions'
    verbose_name = 'GH permissions'
    default_auto_field = 'django.db.models.AutoField'
    default = True

    def ready(self):
        if CANONICAL_BACKEND not in tuple(settings.AUTHENTICATION_BACKENDS):
            raise ImproperlyConfigured(
                'AUTHENTICATION_BACKENDS must include %s.'
                % CANONICAL_BACKEND
            )

        # auth.backends imports auth.models before the app registry is ready.
        from gh_permissions.backends import GhAuthorizationBackend
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
        recorder = GhAuthorizationBackend().get_registry()
        register_collaborator(recorder)
        register_team(recorder)
        register_organization_owner(recorder)
        register_personal_delegation(recorder)
        register_all_personal_repositories_delegation(recorder)
        register_delegation(recorder)
        register_issue_permissions(recorder)
