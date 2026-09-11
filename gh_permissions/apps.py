from django.apps import AppConfig


class GhPermissionsConfig(AppConfig):
    """Contribute GH registrations onto the kernel store during ready()."""

    name = 'gh_permissions'
    label = 'gh_permissions'
    verbose_name = 'GH permissions'
    default_auto_field = 'django.db.models.AutoField'
    default = True

    def ready(self):
        if getattr(self, 'apps', None) is None or not self.apps.is_installed('trusts'):
            return
        from trusts.apps import kernel_config
        from trusts.core import TrustsConfigurationError

        try:
            registry = kernel_config(self.apps).configured_backend().registry
        except (LookupError, TrustsConfigurationError):
            return
        if getattr(self, '_gh_direct_registry_id', None) is registry:
            return
        from gh_permissions.policy import register_direct
        register_direct(registry)
        self._gh_direct_registry_id = registry
