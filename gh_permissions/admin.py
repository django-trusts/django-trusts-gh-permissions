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
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
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
    create_user,
    delete_organization,
    delete_user,
    name_is_taken,
    rename_organization,
    rename_user,
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
    """Ownership rows stay read-only for non-superusers.

    Adding an owner is a domain service. This admin does not call it.
    Non-superusers cannot add, change, or delete the row that grants
    their own ``manage_organization`` authority. Superusers still
    bypass the scope mixin.
    """

    authorization_scope_paths = 'organization'
    scope_allows_add = False
    scope_allows_change = False
    scope_allows_delete = False
    list_display = ('user', 'organization')
    ordering = ('pk',)


class ServiceUserCreationForm(AdminUserCreationForm):
    """Reject a username the shared ledger already holds."""

    def clean_username(self):
        username = super().clean_username()
        if username and name_is_taken(username):
            raise ValidationError('That name is already reserved.')
        return username


class ServiceUserChangeForm(UserChangeForm):
    """Reject a rename onto a name the shared ledger already holds."""

    def clean_username(self):
        username = self.cleaned_data.get('username')
        if not username:
            return username
        if self.instance.pk and username == self.instance.username:
            return username
        if name_is_taken(username, ignoring_user_id=self.instance.pk):
            raise ValidationError('That name is already reserved.')
        return username


class ServiceBackedUserAdmin(UserAdmin):
    """Create, rename, and delete users through the shared-name services.

    Register this on the project's user model. It is not registered
    here, because the library does not own ``AUTH_USER_MODEL``.
    """

    form = ServiceUserChangeForm
    add_form = ServiceUserCreationForm

    def save_model(self, request, obj, form, change):
        if change:
            stored = get_user_model().objects.get(pk=obj.pk)
            if obj.username != stored.username:
                try:
                    rename_user(stored, obj.username)
                except (AliasConflict, ValueError) as exc:
                    raise ValidationError(str(exc))
            super().save_model(request, obj, form, change)
            return
        extra = {}
        for field_name in (
            'email', 'is_staff', 'is_active', 'is_superuser',
            'first_name', 'last_name',
        ):
            if field_name in form.cleaned_data:
                extra[field_name] = form.cleaned_data[field_name]
        try:
            created = create_user(
                form.cleaned_data['username'],
                password=form.cleaned_data.get('password1'),
                **extra,
            )
        except (AliasConflict, ValueError) as exc:
            raise ValidationError(str(exc))
        obj.pk = created.pk
        obj.password = created.password

    def delete_model(self, request, obj):
        delete_user(obj)

    def delete_queryset(self, request, queryset):
        for user in queryset:
            delete_user(user)


admin.site.register(Organization, OrganizationAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(Repository, RepositoryAdmin)
admin.site.register(RepositoryCollaborator, RepositoryCollaboratorAdmin)
admin.site.register(TeamRepositoryPermission, TeamRepositoryPermissionAdmin)
admin.site.register(OrganizationOwnership, OrganizationOwnershipAdmin)
