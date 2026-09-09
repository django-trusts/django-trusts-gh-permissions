from django.apps import AppConfig


class GhPermissionsConfig(AppConfig):
    """Register the GH policy on the process-wide kernel maps."""

    name = 'gh_permissions'
    label = 'gh_permissions'
    verbose_name = 'GH permissions'
    default_auto_field = 'django.db.models.AutoField'
    default = True

    def ready(self):
        from gh_permissions.policy import register_gh_policy

        register_gh_policy()
