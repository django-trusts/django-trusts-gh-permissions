from django.apps import AppConfig

from trusts.core import TrustsRegistry


class GhPermissionsConfig(AppConfig):
    """Register the GH direct root on an isolated C2 registry.

    GH does not list ``TrustModelBackend``. Live C2
    ``AuthorizedManager.authorized`` reads ``configured_handles()``, which
    is AUTHENTICATION_BACKENDS-keyed. Isolated ``TrustsRegistry`` is the
    public instance API this consumer can use without that backend.
    """

    name = 'gh_permissions'
    label = 'gh_permissions'
    verbose_name = 'GH permissions'
    default_auto_field = 'django.db.models.AutoField'
    default = True

    def ready(self):
        from gh_permissions.policy import register_gh

        registry = TrustsRegistry()
        register_gh(registry)
        registry.freeze()
        self.registry = registry
