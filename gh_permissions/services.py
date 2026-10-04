"""Transaction-safe writes for the shared name ledger.

``User.username`` and ``Organization.name`` stay the stored names.
``Alias`` only reserves the current name. The database cannot keep
those columns identical, so these functions are the supported write
path. A raw ``QuerySet`` write, including Django's own user admin and
``User.objects.create``, does not reserve, rename, or release an alias
and does not create a personal organization. That bypass is an
example limitation, not a second implementation of the rules.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, transaction

from gh_permissions.models import Alias, Organization, OrganizationOwnership


OWNER_GROUP_NAME = 'organization-owners'

# Broad organization and repository permissions this example already uses.
OWNER_PERMISSIONS = (
    ('organization', 'manage_organization'),
    ('repository', 'read_repository'),
    ('repository', 'write_repository'),
    ('repository', 'admin_repository'),
)


class AliasConflict(Exception):
    """The shared current-name ledger already holds ``name``."""

    def __init__(self, name):
        self.name = name
        super().__init__('Alias %r is already reserved.' % (name,))


class AliasMissing(Exception):
    """The named object has no current alias row.

    A user or conventional organization created outside these services
    has a real name and no ledger row. Renaming it here would invent a
    reservation the schema cannot prove.
    """

    def __init__(self, name):
        self.name = name
        super().__init__('No alias is reserved for %r.' % (name,))


class PersonalOrganizationError(Exception):
    """A personal organization has no name of its own."""


def _require_name(name, max_length):
    if not isinstance(name, str) or name == '' or name != name.strip():
        raise ValueError('A reserved name must be a non-blank string.')
    if len(name) > max_length:
        raise ValueError(
            'A reserved name must be at most %s characters.' % max_length
        )
    return name


def _require_username(name):
    field = get_user_model()._meta.get_field('username')
    return _require_name(name, field.max_length)


def _require_organization_name(name):
    field = Organization._meta.get_field('name')
    return _require_name(name, field.max_length)


def name_is_taken(name, *, ignoring_user_id=None, ignoring_organization_id=None):
    """True when a ledger row, user, or conventional organization holds ``name``.

    The ledger is not a foreign key, so a raw user or organization row can
    exist without an alias. The service still treats that name as taken.
    """
    if Alias.objects.filter(name=name).exists():
        return True
    users = get_user_model().objects.filter(username=name)
    if ignoring_user_id is not None:
        users = users.exclude(pk=ignoring_user_id)
    if users.exists():
        return True
    organizations = Organization.objects.filter(name=name)
    if ignoring_organization_id is not None:
        organizations = organizations.exclude(pk=ignoring_organization_id)
    return organizations.exists()


def _reserve_alias(name):
    if name_is_taken(name):
        raise AliasConflict(name)
    try:
        with transaction.atomic():
            return Alias.objects.create(name=name)
    except IntegrityError as exc:
        raise AliasConflict(name) from exc


def _locked_alias(name):
    try:
        return Alias.objects.select_for_update().get(name=name)
    except Alias.DoesNotExist as exc:
        raise AliasMissing(name) from exc


def _retarget_alias(alias, name, **ignoring):
    if name_is_taken(name, **ignoring):
        raise AliasConflict(name)
    alias.name = name
    try:
        with transaction.atomic():
            alias.save(update_fields=['name'])
    except IntegrityError as exc:
        raise AliasConflict(name) from exc


def ensure_owner_group():
    """Return the shared owner group, with the example's broad permissions.

    Permission rows are created after migrations, so this is safe to call
    from ``post_migrate`` and from a write that needs the group now.
    """
    permissions = []
    for model_name, codename in OWNER_PERMISSIONS:
        permissions.append(Permission.objects.get(
            content_type__app_label='gh_permissions',
            content_type__model=model_name,
            codename=codename,
        ))
    group, _created = Group.objects.get_or_create(name=OWNER_GROUP_NAME)
    group.permissions.set(permissions)
    return group


def seed_owner_group_on_migrate(sender, **kwargs):
    """Seed the owner group once ``gh_permissions`` permissions exist."""
    del sender, kwargs
    found = []
    for model_name, codename in OWNER_PERMISSIONS:
        permission = Permission.objects.filter(
            content_type__app_label='gh_permissions',
            content_type__model=model_name,
            codename=codename,
        ).first()
        if permission is None:
            return
        found.append(permission)
    group, _created = Group.objects.get_or_create(name=OWNER_GROUP_NAME)
    group.permissions.set(found)


def create_user(username, password=None, **extra):
    """Reserve ``username``, then create the user, personal org, and owner row."""
    if 'username' in extra:
        raise TypeError('Pass username positionally.')
    username = _require_username(username)
    with transaction.atomic():
        _reserve_alias(username)
        user = get_user_model().objects.create_user(
            username=username, password=password, **extra,
        )
        organization = Organization.objects.create(
            personal_user=user,
            owner_group=ensure_owner_group(),
        )
        OrganizationOwnership.objects.create(
            user=user,
            organization=organization,
        )
        return user


def create_organization(name):
    """Reserve ``name``, then create a conventional organization."""
    name = _require_organization_name(name)
    with transaction.atomic():
        _reserve_alias(name)
        return Organization.objects.create(
            name=name,
            owner_group=ensure_owner_group(),
        )


def rename_user(user, username):
    """Rename the alias and ``user.username`` in one transaction."""
    username = _require_username(username)
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        alias = _locked_alias(user.username)
        if username == user.username:
            return user
        _retarget_alias(alias, username, ignoring_user_id=user.pk)
        user.username = username
        try:
            with transaction.atomic():
                user.save(update_fields=['username'])
        except IntegrityError as exc:
            raise AliasConflict(username) from exc
        return user


def rename_organization(organization, name):
    """Rename the alias and a conventional organization's name together."""
    name = _require_organization_name(name)
    with transaction.atomic():
        organization = Organization.objects.select_for_update().get(
            pk=organization.pk,
        )
        if organization.personal_user_id is not None:
            raise PersonalOrganizationError(
                'A personal organization has no independent name.'
            )
        alias = _locked_alias(organization.name)
        if name == organization.name:
            return organization
        _retarget_alias(
            alias, name, ignoring_organization_id=organization.pk,
        )
        organization.name = name
        try:
            with transaction.atomic():
                organization.save(update_fields=['name'])
        except IntegrityError as exc:
            raise AliasConflict(name) from exc
        return organization


def delete_user(user):
    """Delete the user and release the username alias.

    The personal organization and its memberships follow the user.
    A user created outside these services may have no alias; deletion
    still removes the user.
    """
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        Alias.objects.filter(name=user.username).delete()
        user.delete()


def delete_organization(organization):
    """Delete a conventional organization and release its alias."""
    with transaction.atomic():
        organization = Organization.objects.select_for_update().get(
            pk=organization.pk,
        )
        if organization.personal_user_id is not None:
            raise PersonalOrganizationError(
                'Delete the user to release a personal organization.'
            )
        if organization.name:
            Alias.objects.filter(name=organization.name).delete()
        organization.delete()
