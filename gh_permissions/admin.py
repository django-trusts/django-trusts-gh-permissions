"""GH adapter for organization-scoped stock admin.

Scope rows come from ``Organization.objects.authorized(user,
manage_organization)``. An ``OrganizationOwnership`` row is what
puts that organization in the queryset. Conventional organization
create, rename, and delete call the domain services.
Personal-organization deletion deletes the user, which releases the
username alias.

Authorization-bearing edits call the relationship services. A stock
hook does not ``save()`` or write a many-to-many before that service
has authorized the persisted parent and validated the mutation in one
transaction. ``Team.organization`` and ``Repository.organization``
stay on the stored row. ``LastOrganizationOwner`` is a form or
message refusal, not an uncaught error. Private hook plumbing lives
in ``_admin_scope``.
"""

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected as stock_delete_selected
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import router, transaction
from django.http import HttpResponseRedirect

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
    RelationshipWriteError,
    add_organization_owner,
    create_organization,
    create_repository_collaborator,
    create_team_repository_permission,
    create_user,
    delete_organization,
    delete_organization_ownership,
    delete_repository_collaborator,
    delete_team,
    delete_team_repository_permission,
    delete_user,
    move_repository_organization,
    move_team_organization,
    name_is_taken,
    rename_organization,
    rename_user,
    replace_collaborator_permissions,
    replace_team_members_and_ceiling,
    update_organization_ownership,
    update_repository_collaborator,
    update_team_repository_permission,
)


def _guard_scope(model_admin, request, obj, change):
    """Same add/change gate as the scope mixin's ``save_model``."""
    if model_admin.bypasses_scope(request):
        return
    allowed_write = (
        model_admin.scope_allows_change if change
        else model_admin.scope_allows_add
    )
    if not allowed_write or not model_admin._in_scope(request, obj):
        raise PermissionDenied


def _form_with_actor(form, actor):
    """Return a form class that carries the acting user into ``clean``."""

    class ActorBoundForm(form):
        pass

    ActorBoundForm.actor = actor
    return ActorBoundForm


def _pk_list(objects):
    return [item.pk for item in objects]


class ServiceRoutedAdmin:
    """Present a relationship-service refusal without leaving a partial write.

    ``changeform_view`` and the bulk delete action run inside one
    transaction. ``delete_view`` already does. A
    ``RelationshipWriteError`` rolls that transaction back and is shown
    as a message. ``delete_queryset`` calls ``delete_model`` for each
    selected row so the built-in bulk action cannot skip the service.
    """

    actions = ['delete_selected']

    def changeform_view(
        self, request, object_id=None, form_url='', extra_context=None,
    ):
        try:
            with transaction.atomic(using=router.db_for_write(self.model)):
                return super().changeform_view(
                    request, object_id, form_url, extra_context,
                )
        except RelationshipWriteError as exc:
            messages.error(request, str(exc))
            return HttpResponseRedirect(request.get_full_path())

    def delete_view(self, request, object_id, extra_context=None):
        try:
            return super().delete_view(request, object_id, extra_context)
        except RelationshipWriteError as exc:
            messages.error(request, str(exc))
            return HttpResponseRedirect(request.get_full_path())

    def delete_queryset(self, request, queryset):
        pks = list(queryset.order_by('pk').values_list('pk', flat=True))
        with transaction.atomic(using=router.db_for_write(self.model)):
            for pk in pks:
                try:
                    obj = self.model.objects.get(pk=pk)
                except self.model.DoesNotExist:
                    continue
                self.delete_model(request, obj)

    @admin.action(
        permissions=['delete'],
        description=stock_delete_selected.short_description,
    )
    def delete_selected(self, request, queryset):
        try:
            with transaction.atomic(using=router.db_for_write(self.model)):
                return stock_delete_selected(self, request, queryset)
        except RelationshipWriteError as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return None


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


class OrganizationAdmin(ServiceRoutedAdmin, OrgScopedAdmin):
    authorization_scope_paths = ''
    scope_allows_add = False
    ordering = ('pk',)
    form = ConventionalOrganizationForm

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.personal_user_id is not None:
            return ('name',)
        return ()

    def save_model(self, request, obj, form, change):
        _guard_scope(self, request, obj, change)
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


class ImmutableOrganizationForm(forms.ModelForm):
    """Refuse a submitted organization with the move service.

    ``clean`` runs before ``_post_clean`` copies submitted values onto
    the instance, so ``instance.organization_id`` is the stored
    boundary. The service refuses every actor, including an active
    superuser, and writes nothing. ``save_model`` calls it again
    before a name write.
    """

    move_boundary = None

    def clean(self):
        cleaned = super().clean()
        if (
            self.errors
            or not getattr(self.instance, 'pk', None)
            or self.move_boundary is None
        ):
            return cleaned
        organization = cleaned.get('organization')
        if (
            organization is None
            or organization.pk == self.instance.organization_id
        ):
            return cleaned
        try:
            self.move_boundary(
                getattr(self, 'actor', None),
                self.instance.pk,
                organization.pk,
            )
        except RelationshipWriteError as exc:
            raise ValidationError(str(exc)) from exc
        return cleaned


class ImmutableBoundaryAdmin(ServiceRoutedAdmin, OrgScopedAdmin):
    """Name edits stay on the row. The organization foreign key does not.

    The move service is called before any name ``UPDATE``, and this
    method does not lock the row first: the service locks the actor
    and then the parent. ``defer_name_until_related`` leaves the name
    write until after a many-to-many service in ``save_related``.
    """

    move_boundary = None
    defer_name_until_related = False

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        return _form_with_actor(form, request.user)

    def save_model(self, request, obj, form, change):
        _guard_scope(self, request, obj, change)
        if not change:
            obj.save()
            return
        stored = self.model.objects.get(pk=obj.pk)
        if stored.organization_id != obj.organization_id:
            self.move_boundary(
                request.user, stored.pk, obj.organization_id,
            )
        obj.organization_id = stored.organization_id
        if self.defer_name_until_related:
            return
        if stored.name != obj.name:
            stored.name = obj.name
            stored.save(update_fields=['name'])
        obj.name = stored.name


class TeamAdminForm(ImmutableOrganizationForm):
    move_boundary = staticmethod(move_team_organization)

    class Meta:
        model = Team
        fields = ('organization', 'name', 'members', 'allowed_operations')


class TeamAdmin(ImmutableBoundaryAdmin):
    authorization_scope_paths = 'organization'
    ordering = ('pk',)
    form = TeamAdminForm
    move_boundary = staticmethod(move_team_organization)
    defer_name_until_related = True

    def save_related(self, request, form, formsets, change):
        # Do not write the form's many-to-many sets here. The service
        # replaces both after it authorizes the stored organization.
        # The name write follows that call, still inside the change
        # form transaction, so a refusal keeps the previous name.
        replace_team_members_and_ceiling(
            request.user,
            form.instance.pk,
            _pk_list(form.cleaned_data.get('members', ())),
            _pk_list(form.cleaned_data.get('allowed_operations', ())),
        )
        stored = Team.objects.get(pk=form.instance.pk)
        submitted_name = form.cleaned_data.get('name', stored.name)
        if stored.name != submitted_name:
            stored.name = submitted_name
            stored.save(update_fields=['name'])
            form.instance.name = stored.name

    def delete_model(self, request, obj):
        delete_team(request.user, obj.pk)


class RepositoryAdminForm(ImmutableOrganizationForm):
    move_boundary = staticmethod(move_repository_organization)

    class Meta:
        model = Repository
        fields = ('organization', 'name')


class RepositoryAdmin(ImmutableBoundaryAdmin):
    authorization_scope_paths = 'organization'
    ordering = ('pk',)
    form = RepositoryAdminForm
    move_boundary = staticmethod(move_repository_organization)


class RepositoryCollaboratorAdmin(ServiceRoutedAdmin, OrgScopedAdmin):
    authorization_scope_paths = 'repository__organization'
    ordering = ('pk',)

    def save_model(self, request, obj, form, change):
        _guard_scope(self, request, obj, change)

    def save_related(self, request, form, formsets, change):
        obj = form.instance
        permission_ids = _pk_list(form.cleaned_data.get('permissions', ()))
        actor = request.user
        if not change:
            created = create_repository_collaborator(
                actor, obj.repository_id, obj.user_id, permission_ids,
            )
            obj.pk = created.pk
            return
        stored = RepositoryCollaborator.objects.get(pk=obj.pk)
        user_id = obj.user_id if obj.user_id != stored.user_id else None
        repository_id = (
            obj.repository_id
            if obj.repository_id != stored.repository_id
            else None
        )
        with transaction.atomic():
            if user_id is not None or repository_id is not None:
                update_repository_collaborator(
                    actor,
                    stored.pk,
                    user_id=user_id,
                    repository_id=repository_id,
                )
            replace_collaborator_permissions(
                actor, stored.pk, permission_ids,
            )

    def delete_model(self, request, obj):
        delete_repository_collaborator(request.user, obj.pk)


class TeamRepositoryPermissionAdmin(ServiceRoutedAdmin, OrgScopedAdmin):
    authorization_scope_paths = (
        'team__organization',
        'repository__organization',
    )
    ordering = ('pk',)

    def save_model(self, request, obj, form, change):
        _guard_scope(self, request, obj, change)
        actor = request.user
        if not change:
            created = create_team_repository_permission(
                actor, obj.team_id, obj.repository_id, obj.operation_id,
            )
            obj.pk = created.pk
            return
        stored = TeamRepositoryPermission.objects.get(pk=obj.pk)
        team_id = obj.team_id if obj.team_id != stored.team_id else None
        repository_id = (
            obj.repository_id
            if obj.repository_id != stored.repository_id
            else None
        )
        permission_id = (
            obj.operation_id
            if obj.operation_id != stored.operation_id
            else None
        )
        if (
            team_id is None
            and repository_id is None
            and permission_id is None
        ):
            return
        update_team_repository_permission(
            actor,
            stored.pk,
            team_id=team_id,
            repository_id=repository_id,
            permission_id=permission_id,
        )

    def delete_model(self, request, obj):
        delete_team_repository_permission(request.user, obj.pk)


class OrganizationOwnershipAdmin(ServiceRoutedAdmin, OrgScopedAdmin):
    """Non-superusers cannot add, change, or delete an ownership row.

    That disablement is the scoped owner admin. A superuser bypasses
    it, and those writes call the ownership services. The retain-one
    owner rule is the service's, so removing the last owner is a
    refusal rather than a raw delete.
    """

    authorization_scope_paths = 'organization'
    scope_allows_add = False
    scope_allows_change = False
    scope_allows_delete = False
    list_display = ('user', 'organization')
    ordering = ('pk',)

    def save_model(self, request, obj, form, change):
        if not self.bypasses_scope(request):
            raise PermissionDenied
        actor = request.user
        if not change:
            created = add_organization_owner(
                actor, obj.organization_id, obj.user_id,
            )
            obj.pk = created.pk
            return
        stored = OrganizationOwnership.objects.get(pk=obj.pk)
        user_id = obj.user_id if obj.user_id != stored.user_id else None
        organization_id = (
            obj.organization_id
            if obj.organization_id != stored.organization_id
            else None
        )
        if user_id is None and organization_id is None:
            return
        update_organization_ownership(
            actor,
            stored.pk,
            user_id=user_id,
            organization_id=organization_id,
        )

    def delete_model(self, request, obj):
        if not self.bypasses_scope(request):
            raise PermissionDenied
        delete_organization_ownership(request.user, obj.pk)


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


class ServiceBackedUserAdmin(ServiceRoutedAdmin, UserAdmin):
    """Create, rename, and delete users through the shared-name services.

    Register this on the project's user model. It is not registered
    here, because the library does not own ``AUTH_USER_MODEL``.
    ``delete_user`` raises ``LastOrganizationOwner`` when a surviving
    conventional organization would be left with none. The delete
    hooks present that as a message and leave the user in place.
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


admin.site.register(Organization, OrganizationAdmin)
admin.site.register(Team, TeamAdmin)
admin.site.register(Repository, RepositoryAdmin)
admin.site.register(RepositoryCollaborator, RepositoryCollaboratorAdmin)
admin.site.register(TeamRepositoryPermission, TeamRepositoryPermissionAdmin)
admin.site.register(OrganizationOwnership, OrganizationOwnershipAdmin)
