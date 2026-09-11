from django.apps import AppConfig as DjangoAppConfig
from django.core.exceptions import ImproperlyConfigured


CANONICAL_BACKEND_PATH = 'gh_permissions.backends.GhAuthorizationBackend'
CORE_REQUIREMENT = 'django-trusts>=1.0.0.dev2,<2'
FLOOR_MESSAGE = (
    'django-trusts-gh-permissions 0.1.0.dev0 requires %s '
    '(TrustsImplementationConfig and implementation_for_path / '
    'implementation_for_class). The installed django-trusts is below '
    'the Step I floor. Upgrade django-trusts to 1.0.0.dev2 or later.'
    % CORE_REQUIREMENT
)


try:
    from trusts.apps import TrustsImplementationConfig
except ImportError:
    # Raw ImportError is not an accepted IIb fail path. ready() speaks.
    TrustsImplementationConfig = None


_GhBase = TrustsImplementationConfig or DjangoAppConfig


class GhPermissionsConfig(_GhBase):
    """GH implementation owner. Donates direct and team roots onto itself.

    Core is a Python dependency only. This config does not require an
    installed kernel AppConfig and never uses the kernel accessor.
    """

    name = 'gh_permissions'
    label = 'gh_permissions'
    verbose_name = 'GH permissions'
    default_auto_field = 'django.db.models.AutoField'
    default = True
    trusts_backend_paths = (CANONICAL_BACKEND_PATH,)

    def ready(self):
        if TrustsImplementationConfig is None:
            raise ImproperlyConfigured(FLOOR_MESSAGE)
        super(GhPermissionsConfig, self).ready()
        self._donate_gh_policy()

    def _donate_gh_policy(self):
        """Register both independent roots on this owner's store.

        Uses the Step I public owner/resolver API. Never uses the
        kernel accessor. The two registration atoms stay separate.
        """
        from trusts.apps import implementation_for_path
        from gh_permissions.policy import register_direct, register_team

        owner = implementation_for_path(
            CANONICAL_BACKEND_PATH, apps_registry=self.apps,
        )
        handle = owner.configured_backend(CANONICAL_BACKEND_PATH)
        registry = handle.registry
        if getattr(self, '_gh_policy_registry_id', None) is registry:
            return
        register_direct(registry)
        register_team(registry)
        self._gh_policy_registry_id = registry


def gh_config(apps_registry=None):
    """Return the unique installed ``GhPermissionsConfig`` by class identity.

    Optional ``apps_registry`` is an ``Apps`` instance; the default is
    Django's global registry. None or several owners fail loud. Does
    not use the kernel accessor.
    """
    from django.apps import apps as django_apps
    from trusts.core import TrustsConfigurationError

    registry = django_apps if apps_registry is None else apps_registry
    matches = [
        config for config in registry.get_app_configs()
        if type(config) is GhPermissionsConfig
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise TrustsConfigurationError('No installed GhPermissionsConfig.')
    raise TrustsConfigurationError(
        'Multiple GhPermissionsConfig instances: %r' % (matches,)
    )
