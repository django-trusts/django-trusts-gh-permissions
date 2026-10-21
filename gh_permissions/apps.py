"""GH app config. This app owns its Trusts recorder and catalog."""

from django.db.models.signals import post_migrate

from trusts.apps import TrustsConfigMixin


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
