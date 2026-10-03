"""Organization-owner boundary on Django's built-in admin.

One mixin plus a declarative organization path per model. Superusers
keep stock unrestricted ``ModelAdmin`` behavior. Other staff may view
and mutate only rows whose declared organizations are ones they own.
User and ``Operation`` choices stay global grant targets; this module
does not administer user accounts.

No custom templates, routes, or grant-management views.
"""

from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError

from gh_permissions.models import (
    Operation,
    Organization,
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
    contains the first hop, that value wins over the stored instance so
    add/change validation sees the submitted foreign key.
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


def _scope_queryset(queryset, paths, user):
    for path in paths:
        if path == '':
            queryset = queryset.filter(owner=user)
        else:
            queryset = queryset.filter(**{'%s__owner' % path: user})
    return queryset


class OrgScopedAdmin(admin.ModelAdmin):
    """Tenant boundary for organization-owner administration.

    Subclasses declare:

    ``organization_path``
        ``''`` when the model is ``Organization``, a lookup string, or a
        tuple of lookups. Every path must resolve to an organization
        owned by the request user.
    ``alignment_paths``
        Lookups that must resolve to the same organization.
    ``locked_fields``
        Fields a non-superuser cannot change (``Organization.owner``).
    ``owners_may_add``
        When false, only a superuser may add rows.
    """

    organization_path = None
    alignment_paths = ()
    locked_fields = ()
    owners_may_add = True

    def _paths(self):
        return _as_paths(self.organization_path)

    def _organizations(self, obj, cleaned=None):
        return [
            _resolve_organization(obj, path, cleaned=cleaned)
            for path in self._paths()
        ]

    def _in_scope(self, user, obj, cleaned=None):
        organizations = self._organizations(obj, cleaned=cleaned)
        if not organizations:
            return False
        return all(
            organization is not None and organization.owner_id == user.pk
            for organization in organizations
        )

    def _alignment_message(self, obj, cleaned=None):
        if not self.alignment_paths:
            return None
        organizations = [
            _resolve_organization(obj, path, cleaned=cleaned)
            for path in self.alignment_paths
        ]
        if any(organization is None for organization in organizations):
            return 'Repository grants must stay inside one organization.'
        if len({organization.pk for organization in organizations}) != 1:
            return 'Repository grants must stay inside one organization.'
        return None

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return _scope_queryset(queryset, self._paths(), request.user)

    def _scoped_related_queryset(self, model, request):
        if request.user.is_superuser:
            return None
        try:
            model_admin = self.admin_site.get_model_admin(model)
        except admin.sites.NotRegistered:
            return None
        if not isinstance(model_admin, OrgScopedAdmin):
            return None
        return _scope_queryset(
            model._default_manager.all(),
            model_admin._paths(),
            request.user,
        )

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        scoped = self._scoped_related_queryset(db_field.remote_field.model, request)
        if scoped is not None:
            kwargs['queryset'] = scoped
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        scoped = self._scoped_related_queryset(db_field.remote_field.model, request)
        if scoped is not None:
            kwargs['queryset'] = scoped
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            for name in self.locked_fields:
                if name not in fields:
                    fields.append(name)
        return fields

    def has_add_permission(self, request):
        if not super().has_add_permission(request):
            return False
        if request.user.is_superuser:
            return True
        if not self.owners_may_add:
            return False
        return Organization.objects.filter(owner=request.user).exists()

    def _object_permission(self, request, obj):
        if not request.user.is_superuser and obj is not None:
            return self._in_scope(request.user, obj)
        return True

    def has_view_permission(self, request, obj=None):
        if not super().has_view_permission(request, obj):
            return False
        return self._object_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        return self._object_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        return self._object_permission(request, obj)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form_class = super().get_form(request, obj, change=change, **kwargs)
        scoped_admin = self

        class OrganizationScopedForm(form_class):
            def clean(self):
                cleaned = super().clean()
                if self.errors:
                    return cleaned
                if (
                    not request.user.is_superuser
                    and not scoped_admin._in_scope(
                        request.user, self.instance, cleaned=cleaned,
                    )
                ):
                    raise ValidationError(
                        'That row is outside the organizations you own.'
                    )
                message = scoped_admin._alignment_message(
                    self.instance, cleaned=cleaned,
                )
                if message:
                    raise ValidationError(message)
                return cleaned

        return OrganizationScopedForm

    def _restore_locked_fields(self, obj):
        if not obj.pk or not self.locked_fields:
            return
        original = self.model._default_manager.get(pk=obj.pk)
        for name in self.locked_fields:
            field = obj._meta.get_field(name)
            setattr(obj, field.attname, getattr(original, field.attname))

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            self._restore_locked_fields(obj)
            if not self._in_scope(request.user, obj):
                raise PermissionDenied
        if self._alignment_message(obj):
            raise PermissionDenied
        super().save_model(request, obj, form, change)


class OrganizationAdmin(OrgScopedAdmin):
    organization_path = ''
    owners_may_add = False
    locked_fields = ('owner',)
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


admin.site.register(Organization, OrganizationAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(Repository, RepositoryAdmin)
admin.site.register(UserRepositoryPermission, UserRepositoryPermissionAdmin)
admin.site.register(TeamRepositoryPermission, TeamRepositoryPermissionAdmin)
# Global operation catalog. Not organization-scoped. Bare ModelAdmin so a
# superuser can edit it; organization owners are not granted its permissions.
admin.site.register(Operation)
