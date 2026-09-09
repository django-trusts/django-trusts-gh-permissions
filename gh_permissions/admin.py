from django.contrib import admin
from trusts.admin import AuthorizedModelAdmin

from gh_permissions.models import Repository


@admin.register(Repository)
class RepositoryAdmin(AuthorizedModelAdmin):
    """Authorized repository admin. Operations are GH policy data only."""

    list_operation = 'read'
    view_operation = 'read'
    change_operation = 'write'
    delete_operation = 'admin'
    list_display = ('title', 'organization')
