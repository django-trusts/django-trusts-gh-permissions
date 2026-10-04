"""GH adapter for organization-scoped stock admin.

Scope rows come from ``Organization.objects.authorized(user,
manage_organization)``. Registrations stay here. Team/repository
alignment is ``TeamRepositoryPermission.clean``. Reusable hook
plumbing lives in ``_admin_scope``.
"""

from django.contrib import admin
from django.contrib.auth.models import Permission

from gh_permissions._admin_scope import AuthorizedScopeAdminMixin
from gh_permissions.models import (
    Organization,
    OrganizationOwnerPermission,
    Repository,
    Team,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)


class OrgScopedAdmin(AuthorizedScopeAdminMixin, admin.ModelAdmin):
    """Resolve ``manage_organization`` and expose its authorized queryset."""

    management_codename = 'manage_organization'

    def get_authorized_scopes(self, request):
        try:
            permission = Permission.objects.get(
                content_type__app_label='gh_permissions',
                content_type__model='organization',
                codename=self.management_codename,
            )
        except Permission.DoesNotExist:
            return Organization.objects.none()
        return Organization.objects.authorized(request.user, permission)


class OrganizationAdmin(OrgScopedAdmin):
    authorization_scope_paths = ''
    scope_allows_add = False
    ordering = ('pk',)


class TeamAdmin(OrgScopedAdmin):
    authorization_scope_paths = 'organization'
    ordering = ('pk',)


class RepositoryAdmin(OrgScopedAdmin):
    authorization_scope_paths = 'organization'
    ordering = ('pk',)


class UserRepositoryPermissionAdmin(OrgScopedAdmin):
    authorization_scope_paths = 'repository__organization'
    ordering = ('pk',)


class TeamRepositoryPermissionAdmin(OrgScopedAdmin):
    authorization_scope_paths = (
        'team__organization',
        'repository__organization',
    )
    ordering = ('pk',)


class OrganizationOwnerPermissionAdmin(OrgScopedAdmin):
    """Superuser assigns the grant. The owner cannot write it."""

    authorization_scope_paths = 'organization'
    scope_allows_add = False
    scope_allows_change = False
    ordering = ('pk',)


admin.site.register(Organization, OrganizationAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(Repository, RepositoryAdmin)
admin.site.register(UserRepositoryPermission, UserRepositoryPermissionAdmin)
admin.site.register(TeamRepositoryPermission, TeamRepositoryPermissionAdmin)
admin.site.register(OrganizationOwnerPermission, OrganizationOwnerPermissionAdmin)
