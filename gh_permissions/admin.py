"""Organization-owner boundary on Django's built-in admin.

Scope for every hook is ``Organization.objects.authorized(user,
management_operation)``. Superusers keep stock ``ModelAdmin`` behavior.
No custom templates, routes, or grant-management views.
"""

from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError

from gh_permissions.models import (
    Operation,
    Organization,
    OrganizationOwnerPermission,
    Repository,
    Team,
    TeamRepositoryPermission,
    UserRepositoryPermission,
)


def _as_paths(organization_path):
    if organization_path is None:
        raise ImproperlyConfigured(
            'OrgScopedAdmin requires organization_path.'
        )
    if isinstance(organization_path, str):
        return (organization_path,)
    return tuple(organization_path)


def _resolve_organization(obj, path, cleaned=None):
    """Walk ``path`` to an Organization.

    ``path == ''`` means ``obj`` is the organization. When ``cleaned``
    contains the first hop, that value wins so validation sees the
    submitted foreign key before ``ModelForm._post_clean``.
    """
    if path == '':
        return obj
    parts = path.split('__')
    current = obj
    if cleaned is not None and parts[0] in cleaned:
        current = cleaned[parts[0]]
        parts = parts[1:]
    for part in parts:
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


class OrgScopedAdmin(admin.ModelAdmin):
    """Tenant boundary. One authorized queryset, reused by every hook.

    ``organization_path`` is ``''``, a lookup, or a tuple of lookups.
    ``alignment_paths`` must resolve to the same organization.
    ``owners_may_add`` and ``owners_may_change`` close writes. The
    authority grant sets both false so an owner cannot mint or retarget
    it. ``management_operation`` is the Operation code; a missing row
    authorizes nothing.
    """

    organization_path = None
    alignment_paths = ()
    owners_may_add = True
    owners_may_change = True
    management_operation = 'manage'

    def _paths(self):
        return _as_paths(self.organization_path)

    def _managed_organizations(self, user):
        try:
            operation = Operation.objects.get(code=self.management_operation)
        except Operation.DoesNotExist:
            return Organization.objects.none()
        return Organization.objects.authorized(user, operation)

    def _organizations(self, obj, cleaned=None):
        return [
            _resolve_organization(obj, path, cleaned=cleaned)
            for path in self._paths()
        ]

    def _in_scope(self, user, obj, cleaned=None):
        organizations = self._organizations(obj, cleaned=cleaned)
        if not organizations or any(item is None or not item.pk for item in organizations):
            return False
        found = set(
            self._managed_organizations(user).filter(
                pk__in=[item.pk for item in organizations],
            ).values_list('pk', flat=True)
        )
        return all(item.pk in found for item in organizations)

    def _apply_scope(self, queryset, user):
        managed = self._managed_organizations(user)
        for path in self._paths():
            lookup = 'pk__in' if path == '' else '%s__in' % path
            queryset = queryset.filter(**{lookup: managed})
        return queryset

    def _alignment_message(self, obj, cleaned=None):
        if not self.alignment_paths:
            return None
        organizations = [
            _resolve_organization(obj, path, cleaned=cleaned)
            for path in self.alignment_paths
        ]
        if (
            any(item is None for item in organizations)
            or len({item.pk for item in organizations}) != 1
        ):
            return 'Repository grants must stay inside one organization.'
        return None

    def _write_blocked(self, request, obj, cleaned=None):
        if request.user.is_superuser:
            return False
        if not self.owners_may_change:
            return True
        return not self._in_scope(request.user, obj, cleaned=cleaned)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return self._apply_scope(queryset, request.user)

    def _related_queryset(self, model, request):
        if request.user.is_superuser:
            return None
        try:
            model_admin = self.admin_site.get_model_admin(model)
        except admin.sites.NotRegistered:
            return None
        if not isinstance(model_admin, OrgScopedAdmin):
            return None
        return model_admin.get_queryset(request)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        scoped = self._related_queryset(db_field.remote_field.model, request)
        if scoped is not None:
            kwargs['queryset'] = scoped
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        scoped = self._related_queryset(db_field.remote_field.model, request)
        if scoped is not None:
            kwargs['queryset'] = scoped
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def has_add_permission(self, request):
        if not super().has_add_permission(request):
            return False
        if request.user.is_superuser:
            return True
        if not self.owners_may_add:
            return False
        return self._managed_organizations(request.user).exists()

    def has_view_permission(self, request, obj=None):
        if not super().has_view_permission(request, obj):
            return False
        if request.user.is_superuser or obj is None:
            return True
        return self._in_scope(request.user, obj)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None:
            return True
        return not self._write_blocked(request, obj)

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None:
            return True
        return not self._write_blocked(request, obj)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)
        scoped_admin = self

        class OrganizationScopedForm(form_class):
            def clean(self):
                cleaned = super().clean()
                if self.errors:
                    return cleaned
                if scoped_admin._write_blocked(
                    request, self.instance, cleaned=cleaned,
                ):
                    raise ValidationError(
                        'That row is outside the organizations you manage.'
                    )
                message = scoped_admin._alignment_message(
                    self.instance, cleaned=cleaned,
                )
                if message:
                    raise ValidationError(message)
                return cleaned

        return OrganizationScopedForm

    def save_model(self, request, obj, form, change):
        if self._write_blocked(request, obj):
            raise PermissionDenied
        if self._alignment_message(obj):
            raise PermissionDenied
        super().save_model(request, obj, form, change)


class OrganizationAdmin(OrgScopedAdmin):
    organization_path = ''
    owners_may_add = False
    ordering = ('pk',)


class TeamAdmin(OrgScopedAdmin):
    organization_path = 'organization'
    ordering = ('pk',)


class RepositoryAdmin(OrgScopedAdmin):
    organization_path = 'organization'
    ordering = ('pk',)


class UserRepositoryPermissionAdmin(OrgScopedAdmin):
    organization_path = 'repository__organization'
    ordering = ('pk',)


class TeamRepositoryPermissionAdmin(OrgScopedAdmin):
    organization_path = (
        'team__organization',
        'repository__organization',
    )
    alignment_paths = (
        'team__organization',
        'repository__organization',
    )
    ordering = ('pk',)


class OrganizationOwnerPermissionAdmin(OrgScopedAdmin):
    """Superuser assigns the grant. The owner cannot write it."""

    organization_path = 'organization'
    owners_may_add = False
    owners_may_change = False
    ordering = ('pk',)


admin.site.register(Organization, OrganizationAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(Repository, RepositoryAdmin)
admin.site.register(UserRepositoryPermission, UserRepositoryPermissionAdmin)
admin.site.register(TeamRepositoryPermission, TeamRepositoryPermissionAdmin)
admin.site.register(OrganizationOwnerPermission, OrganizationOwnerPermissionAdmin)
# Global operation catalog. Not organization-scoped. Bare ModelAdmin so a
# superuser can edit it; organization owners are not granted its permissions.
admin.site.register(Operation)
