"""GH adapter for organization-scoped stock admin.

Scope rows come from ``Organization.objects.authorized(user,
manage_organization)``. An ``OrganizationOwnership`` row is what
puts that organization in the queryset. Conventional organization
create, rename, and delete call the domain services.
Personal-organization deletion deletes the user, which releases the
username alias. Private hook plumbing lives in ``_admin_scope``.
"""

from django import forms
from django.contrib import admin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError

from gh_permissions._admin_scope import AuthorizedScopeAdminMixin
from gh_permissions.models import (
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import (
    AliasConflict,
    create_organization,
    delete_organization,
    delete_user,
    name_is_taken,
    rename_organization,
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


class ConventionalOrganizationForm(forms.ModelForm):
    """Name field for the conventional-organization service.

    Personal rows keep a null name. The form does not assign
    ``owner_group`` or ``personal_user``.
    """

    class Meta:
        model = Organization
        fields = ('name',)

    def clean_name(self):
        name = self.cleaned_data.get('name')
        if self.instance.personal_user_id is not None:
            return self.instance.name
        if not isinstance(name, str) or name == '' or name != name.strip():
            raise ValidationError(
                'A conventional organization needs a name.'
            )
        if name_is_taken(name, ignoring_organization_id=self.instance.pk):
            raise ValidationError('That name is already reserved.')
        return name


class OrganizationAdmin(OrgScopedAdmin):
    authorization_scope_paths = ''
    scope_allows_add = False
    ordering = ('pk',)
    form = ConventionalOrganizationForm

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.personal_user_id is not None:
            return ('name',)
        return ()

    def _reject_out_of_scope(self, request, obj, change):
        if self.bypasses_scope(request):
            return
        allowed_write = (
            self.scope_allows_change if change else self.scope_allows_add
        )
        if not allowed_write or not self._in_scope(request, obj):
            raise PermissionDenied

    def save_model(self, request, obj, form, change):
        self._reject_out_of_scope(request, obj, change)
        if getattr(obj, 'personal_user_id', None):
            return
        try:
            if change:
                stored = Organization.objects.get(pk=obj.pk)
                if obj.name != stored.name:
                    stored = rename_organization(stored, obj.name)
                obj.name = stored.name
                return
            created = create_organization(obj.name)
        except (AliasConflict, ValueError) as exc:
            raise ValidationError(str(exc))
        obj.pk = created.pk
        obj.name = created.name
        obj.owner_group_id = created.owner_group_id
        obj.personal_user_id = None

    def delete_model(self, request, obj):
        if obj.personal_user_id is not None:
            delete_user(obj.personal_user)
            return
        delete_organization(obj)

    def delete_queryset(self, request, queryset):
        for organization in queryset:
            self.delete_model(request, organization)


class TeamAdmin(OrgScopedAdmin):
    authorization_scope_paths = 'organization'
    ordering = ('pk',)


class RepositoryAdmin(OrgScopedAdmin):
    authorization_scope_paths = 'organization'
    ordering = ('pk',)


class RepositoryCollaboratorAdmin(OrgScopedAdmin):
    authorization_scope_paths = 'repository__organization'
    ordering = ('pk',)


class TeamRepositoryPermissionAdmin(OrgScopedAdmin):
    authorization_scope_paths = (
        'team__organization',
        'repository__organization',
    )
    ordering = ('pk',)


class OrganizationOwnershipAdmin(OrgScopedAdmin):
    authorization_scope_paths = 'organization'
    ordering = ('pk',)


admin.site.register(Organization, OrganizationAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(Repository, RepositoryAdmin)
admin.site.register(RepositoryCollaborator, RepositoryCollaboratorAdmin)
admin.site.register(TeamRepositoryPermission, TeamRepositoryPermissionAdmin)
admin.site.register(OrganizationOwnership, OrganizationOwnershipAdmin)
