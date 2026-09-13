from django.core.exceptions import ImproperlyConfigured

CORE_REQUIREMENT = 'django-trusts>=1.0.0.dev3,<2'
FLOOR_MESSAGE = (
    'django-trusts-gh-permissions 0.1.0.dev0 requires '
    '%s (TrustsImplementationConfig). '
    'Upgrade django-trusts; do not rely on a missing import.'
    % CORE_REQUIREMENT
)


def _load_implementation_config():
    try:
        from trusts.apps import TrustsImplementationConfig as imported
    except ImportError:
        raise ImproperlyConfigured(FLOOR_MESSAGE)
    return imported


TrustsImplementationConfig = _load_implementation_config()


CANONICAL_BACKEND = 'gh_permissions.backends.GhAuthorizationBackend'


def gh_config(apps_registry=None):
    """Return the installed ``GhPermissionsConfig`` by class identity."""
    from django.apps import apps as django_apps

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


class GhPermissionsConfig(TrustsImplementationConfig):
    """GH implementation owner. Core is a library, not an installed app.

    Owns the exact path ``gh_permissions.backends.GhAuthorizationBackend``.
    ``ready()`` donates through ``implementation_for_path`` and never
    calls ``kernel_config()`` or silently skips.
    """

    name = 'gh_permissions'
    label = 'gh_permissions'
    verbose_name = 'GH permissions'
    default_auto_field = 'django.db.models.AutoField'
    default = True
    trusts_backend_paths = (CANONICAL_BACKEND,)

    def ready(self):
        from trusts.apps import implementation_for_path

        from gh_permissions.policy import register_direct, register_team

        super(GhPermissionsConfig, self).ready()

        owner = implementation_for_path(
            CANONICAL_BACKEND, apps_registry=getattr(self, 'apps', None),
        )
        handle = owner.configured_backend(CANONICAL_BACKEND)
        if getattr(self, '_gh_policy_handle_id', None) == handle:
            return
        register_direct(handle)
        register_team(handle)
        self._gh_policy_handle_id = handle
