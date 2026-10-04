"""Private stock-admin scope plumbing for one bounded configuration.

The application supplies one authorized-scope queryset and declarative
paths from each model to that scope. Paths are ``''`` or forward
single-valued foreign-key or one-to-one lookups. Every path ends at
the same scope model, and multiple paths are AND. Authorized scopes
and protected objects use the same database. This module does not
know the application's models and does not import Trusts.

A relation filters related choices only when that model has a
registered scoped admin. Stock admin routes only. ``check()`` rejects
inlines, ``list_editable``, raw-id fields, autocomplete fields, and
any action other than ``delete_selected``. Many-to-many paths, reverse
paths, multiple databases, dynamic inlines, and custom forms are
outside this contract.
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


def _resolve_scope(obj, path):
    """Walk a forward path to a scope row.

    ``path == ''`` means ``obj`` is the scope row.
    """
    if path == '':
        return obj
    current = obj
    for part in path.split('__'):
        if current is None:
            return None
        current = getattr(current, part, None)
    return current


class AuthorizedScopeAdminMixin:
    """Filter stock admin hooks by ``get_authorized_scopes(request)``.

    ``authorization_scope_paths`` is ``''``, one forward single-valued
    lookup, or a tuple of them. Every path must end on the authorized
    queryset's concrete model in the same database. ``scope_allows_add``,
    ``scope_allows_change``, and ``scope_allows_delete`` are independent.
    ``bypasses_scope`` skips the filter; the default is a superuser.
    """

    authorization_scope_paths = None
    scope_allows_add = True
    scope_allows_change = True
    scope_allows_delete = True

    def get_authorized_scopes(self, request):
        raise NotImplementedError

    def bypasses_scope(self, request):
        user = getattr(request, 'user', None)
        return bool(getattr(user, 'is_superuser', False))

    def _paths(self):
        return _as_paths(self.authorization_scope_paths)

    def _scope_rows(self, obj):
        return [_resolve_scope(obj, path) for path in self._paths()]

    def _in_scope(self, request, obj):
        rows = self._scope_rows(obj)
        if not rows:
            return False
        allowed = self.get_authorized_scopes(request)
        scope_model = allowed.model._meta.concrete_model
        pks = []
        for row in rows:
            if row is None or not getattr(row, 'pk', None):
                return False
            if row._meta.concrete_model is not scope_model:
                return False
            if row._state.db != allowed.db:
                return False
            pks.append(row.pk)
        found = set(allowed.filter(pk__in=pks).values_list('pk', flat=True))
        return all(pk in found for pk in pks)

    def _apply_scope(self, queryset, request):
        allowed = self.get_authorized_scopes(request)
        if queryset.db != allowed.db:
            return queryset.none()
        scope_model = allowed.model._meta.concrete_model
        for path in self._paths():
            if (
                path == ''
                and queryset.model._meta.concrete_model is not scope_model
            ):
                return queryset.none()
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
        if not self.scope_allows_delete:
            return False
        return self._in_scope(request, obj)

    def save_model(self, request, obj, form, change):
        if not self.bypasses_scope(request) and (
            not self.scope_allows_change or not self._in_scope(request, obj)
        ):
            raise PermissionDenied
        super().save_model(request, obj, form, change)
