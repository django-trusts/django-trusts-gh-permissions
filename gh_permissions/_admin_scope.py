"""Reusable stock-admin scope plumbing.

The application supplies one authorized-scope queryset and declarative
paths from each model to that scope. This module does not know the
application's models and does not import Trusts.

Unsupported and rejected by ``check()``: inlines, ``list_editable``,
raw-id fields, autocomplete fields, and any action other than
``delete_selected``.
"""

from django.contrib import admin
from django.core.checks import Error
from django.core.exceptions import ImproperlyConfigured, PermissionDenied


def _as_paths(scope_paths):
    if scope_paths is None:
        raise ImproperlyConfigured(
            'AuthorizedScopeAdminMixin requires authorization_scope_paths.'
        )
    if isinstance(scope_paths, str):
        return (scope_paths,)
    return tuple(scope_paths)


def _resolve_scope(obj, path, cleaned=None):
    """Walk ``path`` to a scope row.

    ``path == ''`` means ``obj`` is the scope row. When ``cleaned``
    contains the first hop, that value wins so a pre-save check sees
    the foreign key ``save_form`` has not written yet.
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


class AuthorizedScopeAdminMixin:
    """Filter stock admin hooks by ``get_authorized_scopes(request)``.

    ``authorization_scope_paths`` is ``''``, a lookup, or a tuple of
    lookups. Every path must land in the authorized queryset.
    ``scope_allows_add`` gates adds. ``scope_allows_change`` false
    denies change and delete. ``bypasses_scope`` skips the filter;
    the default is a superuser.
    """

    authorization_scope_paths = None
    scope_allows_add = True
    scope_allows_change = True

    def get_authorized_scopes(self, request):
        raise NotImplementedError

    def bypasses_scope(self, request):
        user = getattr(request, 'user', None)
        return bool(getattr(user, 'is_superuser', False))

    def _paths(self):
        return _as_paths(self.authorization_scope_paths)

    def _scope_rows(self, obj, cleaned=None):
        return [
            _resolve_scope(obj, path, cleaned=cleaned) for path in self._paths()
        ]

    def _in_scope(self, request, obj, cleaned=None):
        rows = self._scope_rows(obj, cleaned=cleaned)
        if not rows or any(row is None or not row.pk for row in rows):
            return False
        found = set(
            self.get_authorized_scopes(request).filter(
                pk__in=[row.pk for row in rows],
            ).values_list('pk', flat=True)
        )
        return all(row.pk in found for row in rows)

    def _apply_scope(self, queryset, request):
        allowed = self.get_authorized_scopes(request)
        for path in self._paths():
            lookup = 'pk__in' if path == '' else '%s__in' % path
            queryset = queryset.filter(**{lookup: allowed})
        return queryset

    def check(self, **kwargs):
        errors = super().check(**kwargs)
        blocked = (
            ('inlines', 'inlines'),
            ('list_editable', 'list_editable'),
            ('raw_id_fields', 'raw_id_fields'),
            ('autocomplete_fields', 'autocomplete_fields'),
        )
        for attr, label in blocked:
            if getattr(self, attr, ()):
                errors.append(Error(
                    'AuthorizedScopeAdminMixin does not cover %s.' % label,
                    hint='Leave %s empty.' % label,
                    obj=self.__class__,
                    id='admin_scope.E001',
                ))
        actions = getattr(self, 'actions', ())
        if actions not in (None, (), [], ['delete_selected'], ('delete_selected',)):
            errors.append(Error(
                'AuthorizedScopeAdminMixin does not cover custom actions.',
                hint='Leave actions unset, or set only delete_selected.',
                obj=self.__class__,
                id='admin_scope.E002',
            ))
        return errors

    def get_actions(self, request, **kwargs):
        actions = super().get_actions(request, **kwargs)
        unknown = [name for name in actions if name != 'delete_selected']
        if unknown:
            raise ImproperlyConfigured(
                'AuthorizedScopeAdminMixin does not cover actions %s.'
                % (unknown,)
            )
        return actions

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if self.bypasses_scope(request):
            return queryset
        return self._apply_scope(queryset, request)

    def _related_queryset(self, model, request):
        if self.bypasses_scope(request):
            return None
        try:
            model_admin = self.admin_site.get_model_admin(model)
        except admin.sites.NotRegistered:
            return None
        if not isinstance(model_admin, AuthorizedScopeAdminMixin):
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
        if self.bypasses_scope(request):
            return True
        if not self.scope_allows_add:
            return False
        return self.get_authorized_scopes(request).exists()

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or self.bypasses_scope(request):
            return True
        return self.scope_allows_change

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or self.bypasses_scope(request):
            return True
        if not self.scope_allows_change:
            return False
        return self._in_scope(request, obj)

    def save_model(self, request, obj, form, change):
        if not self.bypasses_scope(request) and (
            not self.scope_allows_change or not self._in_scope(request, obj)
        ):
            raise PermissionDenied
        super().save_model(request, obj, form, change)
