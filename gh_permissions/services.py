"""Transaction-safe writes for names and authorization relationships.

``User.username`` and ``Organization.name`` stay the stored names.
``Alias`` only reserves the current name. The database cannot keep
those columns identical, so the name functions are the supported write
path. ``ServiceBackedUserAdmin`` calls them for user create, rename,
and delete. A raw ``QuerySet`` write, including
``User.objects.create``, does not reserve, rename, or release an alias
and does not create a personal organization.

The relationship functions take an actor. Each one runs in one
``transaction.atomic``, locks the persisted organization with
``select_for_update``, and only then evaluates ``manage_organization``.
``create_repository`` locks the submitted organization, then inserts.
``delete_repository`` locks the stored repository and that
repository's organization, then deletes. Collaborator rows and team
grants cascade only after that authorization.
The inquiry is ``Organization.objects.authorized`` on that locked row.
It reads stored ``OrganizationOwnership`` rows. A submitted instance,
a new ownership row, or a permission bundle is not that evidence.

An active superuser (``is_active`` and ``is_superuser``) skips only
the inquiry. A persisted inactive actor is denied before that bypass
and before the ownership inquiry, whether or not an ownership row
exists. Missing parents, duplicate targets, and a team grant whose
team and repository organizations differ still fail, including for a
superuser. ``.authorized`` does not list a superuser who has no
ownership row.

Ownership update and delete keep at least one owner on every
surviving conventional organization. ``delete_user`` locks each
conventional organization that user owns, in primary-key order, and
rolls the deletion back when any of them would be left with none.
The personal organization is deleted with the user, so it is not one
of those survivors. ``Team.organization`` and
``Repository.organization`` stay where they were stored: moving either
row would re-scope collaborator bundles and team grants, and this
slice does not decide whether those grants follow the row. A raw
queryset write does not run these checks. ``register(condition=)`` is
read-time SQL over existing trust rows and is not where the owner
count is enforced.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import IntegrityError, transaction

from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)


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

    The personal organization and its ownership rows follow the user,
    so that organization is not required to keep an owner. Every
    conventional organization this user owns is locked in
    primary-key order inside the same transaction. After each of
    those locks, a ``select_for_update`` recount decides whether
    another owner remains. When any organization would be left with
    no owner, ``LastOrganizationOwner`` lists each of them and
    nothing is deleted. A user created outside these services may
    have no alias; deletion still removes the user when the preflight
    passes. A raw ``User.delete`` does not run it.
    """
    User = get_user_model()
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        _refuse_ownerless_conventional_organizations(user)
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


class RelationshipWriteError(Exception):
    """A supported relationship write was rejected before mutation."""


class UndefinedRelationshipWrite(RelationshipWriteError):
    """The service does not define this write shape."""

    def __init__(self, shape):
        self.shape = shape
        super().__init__('Undefined relationship write: %s.' % (shape,))


class MissingRelationshipTarget(RelationshipWriteError):
    """A submitted primary key is not one persisted row."""

    def __init__(self, label, pk):
        self.label = label
        self.pk = pk
        super().__init__('No %s row exists for %r.' % (label, pk))


class DuplicateRelationshipTarget(RelationshipWriteError):
    """A primary key was repeated, or the relationship already exists."""

    def __init__(self, label):
        self.label = label
        super().__init__(
            'Duplicate %s in this relationship write.' % (label,)
        )


class CrossOrganizationRelationship(RelationshipWriteError):
    """The team organization and the repository organization differ."""

    def __init__(self, team_organization_id, repository_organization_id):
        self.team_organization_id = team_organization_id
        self.repository_organization_id = repository_organization_id
        super().__init__(
            'Repository grants must stay inside one organization.'
        )


class ManagementDenied(RelationshipWriteError):
    """The actor does not manage the locked organization."""

    def __init__(self, organization_id):
        self.organization_id = organization_id
        super().__init__(
            'manage_organization was denied for organization %s.'
            % (organization_id,)
        )


class LastOrganizationOwner(RelationshipWriteError):
    """A surviving conventional organization would have no owner.

    ``organization_ids`` lists every conventional organization this
    write would empty, in ascending primary-key order. The personal
    organization deleted with its user is not included.
    """

    def __init__(self, organization_ids):
        self.organization_ids = tuple(sorted(organization_ids))
        super().__init__(
            'A conventional organization must keep an owner: %s.'
            % ', '.join(str(pk) for pk in self.organization_ids)
        )


class ImmutableOrganizationBoundary(RelationshipWriteError):
    """``Team.organization`` or ``Repository.organization`` was submitted."""

    def __init__(self, label):
        self.label = label
        super().__init__('%s.organization cannot be moved.' % (label,))


def _require_pk(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        raise UndefinedRelationshipWrite(label)
    return value


def _require_pk_sequence(values, label):
    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, (list, tuple))
    ):
        raise UndefinedRelationshipWrite(label)
    seen = set()
    pks = []
    for value in values:
        pk = _require_pk(value, label)
        if pk in seen:
            raise DuplicateRelationshipTarget(label)
        seen.add(pk)
        pks.append(pk)
    return pks


def _locked_one(model, pk, label):
    rows = list(model.objects.select_for_update().filter(pk=pk))
    if len(rows) != 1:
        if len(rows) > 1:
            raise DuplicateRelationshipTarget(label)
        raise MissingRelationshipTarget(label, pk)
    return rows[0]


def _locked_many(model, pks, label):
    if not pks:
        return []
    rows = list(model.objects.select_for_update().filter(pk__in=pks))
    found = {row.pk: row for row in rows}
    if len(rows) != len(found):
        raise DuplicateRelationshipTarget(label)
    if len(found) != len(pks):
        missing = next(pk for pk in pks if pk not in found)
        raise MissingRelationshipTarget(label, missing)
    return [found[pk] for pk in pks]


def _locked_actor(actor):
    user_model = get_user_model()
    if not isinstance(actor, user_model) or actor.pk is None:
        raise UndefinedRelationshipWrite('actor')
    return _locked_one(user_model, actor.pk, 'actor')


def _inquiry_bypassed(actor):
    """Same active-superuser rule as ``User.has_perm``.

    This skips only the ``manage_organization`` inquiry. It is not an
    ownership row, and it does not skip existence or same-organization
    checks. Inactive actors are rejected before this is consulted.
    """
    return bool(actor.is_active and actor.is_superuser)


def _manage_permission():
    return Permission.objects.get(
        content_type__app_label='gh_permissions',
        content_type__model='organization',
        codename='manage_organization',
    )


def _require_manage(actor, organizations):
    """Require ``manage_organization`` on each locked organization.

    ``actor`` is the row reloaded inside the write. An inactive actor
    is denied before the active-superuser bypass and before
    ``Organization.objects.authorized``, matching ``User.has_perm``.
    """
    if not actor.is_active:
        raise ManagementDenied(organizations[0].pk)
    if _inquiry_bypassed(actor):
        return
    permission = _manage_permission()
    seen = set()
    for organization in organizations:
        if organization.pk in seen:
            continue
        seen.add(organization.pk)
        allowed = Organization.objects.authorized(
            actor, permission,
        ).filter(pk=organization.pk)
        if not allowed.exists():
            raise ManagementDenied(organization.pk)


def _lock_organization(organization_id):
    return _locked_one(Organization, organization_id, 'organization')


def _lock_team(team_id):
    return _locked_one(Team, _require_pk(team_id, 'team'), 'team')


def _lock_repository(repository_id):
    return _locked_one(
        Repository, _require_pk(repository_id, 'repository'), 'repository',
    )


def _lock_user(user_id):
    return _locked_one(
        get_user_model(), _require_pk(user_id, 'user'), 'user',
    )


def _lock_users(user_ids):
    return _locked_many(
        get_user_model(),
        _require_pk_sequence(user_ids, 'user'),
        'user',
    )


def _lock_permission(permission_id):
    return _locked_one(
        Permission,
        _require_pk(permission_id, 'permission'),
        'permission',
    )


def _lock_permissions(permission_ids):
    return _locked_many(
        Permission,
        _require_pk_sequence(permission_ids, 'permission'),
        'permission',
    )


def _lock_parent_organization(parent):
    return _lock_organization(parent.organization_id)


def _lock_grant(grant_id):
    return _locked_one(
        TeamRepositoryPermission,
        _require_pk(grant_id, 'team repository permission'),
        'team repository permission',
    )


def _lock_team_and_repository_organizations(team, repository):
    """Lock the stored organization on each side of a team grant."""
    team_organization = _lock_parent_organization(team)
    if repository.organization_id == team_organization.pk:
        repository_organization = team_organization
    else:
        repository_organization = _lock_organization(
            repository.organization_id,
        )
    return team_organization, repository_organization


def _require_same_organization(team_organization, repository_organization):
    if team_organization.pk != repository_organization.pk:
        raise CrossOrganizationRelationship(
            team_organization.pk, repository_organization.pk,
        )


def _lock_collaborator(collaborator_id):
    return _locked_one(
        RepositoryCollaborator,
        _require_pk(collaborator_id, 'repository collaborator'),
        'repository collaborator',
    )


def add_organization_owner(actor, organization_id, user_id):
    """Add one owner of a persisted organization.

    This is the supported ``OrganizationOwnership`` create and the
    supported ``Organization.owners`` add. The new row is not the
    inquiry, including when ``user_id`` is the actor.
    """
    organization_pk = _require_pk(organization_id, 'organization')
    user_pk = _require_pk(user_id, 'user')
    with transaction.atomic():
        actor = _locked_actor(actor)
        organization = _lock_organization(organization_pk)
        _require_manage(actor, [organization])
        user = _lock_user(user_pk)
        if OrganizationOwnership.objects.filter(
            user=user, organization=organization,
        ).exists():
            raise DuplicateRelationshipTarget('organization ownership')
        return OrganizationOwnership.objects.create(
            user=user, organization=organization,
        )


def replace_team_members_and_ceiling(
    actor, team_id, user_ids, permission_ids,
):
    """Replace ``Team.members`` and ``Team.allowed_operations`` together.

    Both sequences are the full new sets. Users are global grant
    targets. Permission rows are global ``auth.Permission`` values.
    The team's stored organization is the boundary.
    ``move_team_organization`` refuses a change to ``Team.organization``.
    """
    team_pk = _require_pk(team_id, 'team')
    user_pks = _require_pk_sequence(user_ids, 'user')
    permission_pks = _require_pk_sequence(permission_ids, 'permission')
    with transaction.atomic():
        actor = _locked_actor(actor)
        team = _lock_team(team_pk)
        organization = _lock_parent_organization(team)
        _require_manage(actor, [organization])
        users = _locked_many(get_user_model(), user_pks, 'user')
        permissions = _locked_many(Permission, permission_pks, 'permission')
        team.members.set(users)
        team.allowed_operations.set(permissions)
        return team


def create_repository_collaborator(
    actor, repository_id, user_id, permission_ids,
):
    """Create one collaborator and set that collaborator's bundle.

    The repository's stored organization is the boundary. The insert
    and the bundle write share one transaction.
    """
    repository_pk = _require_pk(repository_id, 'repository')
    user_pk = _require_pk(user_id, 'user')
    permission_pks = _require_pk_sequence(permission_ids, 'permission')
    with transaction.atomic():
        actor = _locked_actor(actor)
        repository = _lock_repository(repository_pk)
        organization = _lock_parent_organization(repository)
        _require_manage(actor, [organization])
        user = _lock_user(user_pk)
        permissions = _locked_many(Permission, permission_pks, 'permission')
        if RepositoryCollaborator.objects.filter(
            user=user, repository=repository,
        ).exists():
            raise DuplicateRelationshipTarget('repository collaborator')
        collaborator = RepositoryCollaborator.objects.create(
            user=user, repository=repository,
        )
        collaborator.permissions.set(permissions)
        return collaborator


def update_repository_collaborator(
    actor, collaborator_id, *, user_id=None, repository_id=None,
):
    """Change the stored collaborator's user, repository, or both.

    A different repository authorizes that repository's stored
    organization as well as the collaborator's stored organization.
    This does not replace ``permissions``.
    ``move_repository_organization`` refuses a change to
    ``Repository.organization``.
    """
    if user_id is None and repository_id is None:
        raise UndefinedRelationshipWrite('repository collaborator update')
    collaborator_pk = _require_pk(collaborator_id, 'repository collaborator')
    user_pk = None if user_id is None else _require_pk(user_id, 'user')
    repository_pk = (
        None if repository_id is None
        else _require_pk(repository_id, 'repository')
    )
    with transaction.atomic():
        actor = _locked_actor(actor)
        collaborator = _lock_collaborator(collaborator_pk)
        stored = _lock_repository(collaborator.repository_id)
        stored_organization = _lock_parent_organization(stored)
        _require_manage(actor, [stored_organization])
        repository = stored
        if repository_pk is not None:
            if repository_pk != stored.pk:
                repository = _lock_repository(repository_pk)
                replacement = _lock_parent_organization(repository)
                _require_manage(actor, [replacement])
        if user_pk is None:
            user = _lock_user(collaborator.user_id)
        else:
            user = _lock_user(user_pk)
        conflict = RepositoryCollaborator.objects.filter(
            user=user, repository=repository,
        ).exclude(pk=collaborator.pk)
        if conflict.exists():
            raise DuplicateRelationshipTarget('repository collaborator')
        collaborator.user = user
        collaborator.repository = repository
        collaborator.save(update_fields=['user', 'repository'])
        return collaborator


def replace_collaborator_permissions(actor, collaborator_id, permission_ids):
    """Replace the stored collaborator's permission bundle."""
    collaborator_pk = _require_pk(collaborator_id, 'repository collaborator')
    permission_pks = _require_pk_sequence(permission_ids, 'permission')
    with transaction.atomic():
        actor = _locked_actor(actor)
        collaborator = _lock_collaborator(collaborator_pk)
        repository = _lock_repository(collaborator.repository_id)
        organization = _lock_parent_organization(repository)
        _require_manage(actor, [organization])
        permissions = _locked_many(Permission, permission_pks, 'permission')
        collaborator.permissions.set(permissions)
        return collaborator


def delete_repository_collaborator(actor, collaborator_id):
    """Delete the stored collaborator after authorizing its organization."""
    collaborator_pk = _require_pk(collaborator_id, 'repository collaborator')
    with transaction.atomic():
        actor = _locked_actor(actor)
        collaborator = _lock_collaborator(collaborator_pk)
        repository = _lock_repository(collaborator.repository_id)
        organization = _lock_parent_organization(repository)
        _require_manage(actor, [organization])
        collaborator.delete()


def create_team_repository_permission(
    actor, team_id, repository_id, permission_id,
):
    """Create one team-repository grant inside one organization.

    ``QuerySet.create`` does not call ``clean``. This function compares
    the locked team organization with the locked repository organization.
    """
    team_pk = _require_pk(team_id, 'team')
    repository_pk = _require_pk(repository_id, 'repository')
    permission_pk = _require_pk(permission_id, 'permission')
    with transaction.atomic():
        actor = _locked_actor(actor)
        team = _lock_team(team_pk)
        repository = _lock_repository(repository_pk)
        team_organization, repository_organization = (
            _lock_team_and_repository_organizations(team, repository)
        )
        _require_manage(actor, [team_organization, repository_organization])
        _require_same_organization(
            team_organization, repository_organization,
        )
        permission = _lock_permission(permission_pk)
        if TeamRepositoryPermission.objects.filter(
            team=team, repository=repository, operation=permission,
        ).exists():
            raise DuplicateRelationshipTarget('team repository permission')
        return TeamRepositoryPermission.objects.create(
            team=team, repository=repository, operation=permission,
        )


def update_team_repository_permission(
    actor,
    grant_id,
    *,
    team_id=None,
    repository_id=None,
    permission_id=None,
):
    """Update a stored team-repository grant.

    The stored team and repository must already share an organization,
    and that organization must be managed. A replacement team or
    repository is authorized on its own stored organization. The
    resulting pair must still share one organization.
    """
    if (
        team_id is None
        and repository_id is None
        and permission_id is None
    ):
        raise UndefinedRelationshipWrite(
            'team repository permission update',
        )
    grant_pk = _require_pk(grant_id, 'team repository permission')
    team_pk = None if team_id is None else _require_pk(team_id, 'team')
    repository_pk = (
        None if repository_id is None
        else _require_pk(repository_id, 'repository')
    )
    permission_pk = (
        None if permission_id is None
        else _require_pk(permission_id, 'permission')
    )
    with transaction.atomic():
        actor = _locked_actor(actor)
        grant = _lock_grant(grant_pk)
        team = _lock_team(grant.team_id)
        repository = _lock_repository(grant.repository_id)
        team_organization, repository_organization = (
            _lock_team_and_repository_organizations(team, repository)
        )
        _require_manage(actor, [team_organization, repository_organization])
        _require_same_organization(
            team_organization, repository_organization,
        )
        if team_pk is not None and team_pk != team.pk:
            team = _lock_team(team_pk)
            team_organization = _lock_parent_organization(team)
            _require_manage(actor, [team_organization])
        if repository_pk is not None and repository_pk != repository.pk:
            repository = _lock_repository(repository_pk)
            repository_organization = _lock_parent_organization(repository)
            _require_manage(actor, [repository_organization])
        _require_same_organization(
            team_organization, repository_organization,
        )
        if permission_pk is None:
            permission = _lock_permission(grant.operation_id)
        else:
            permission = _lock_permission(permission_pk)
        conflict = TeamRepositoryPermission.objects.filter(
            team=team, repository=repository, operation=permission,
        ).exclude(pk=grant.pk)
        if conflict.exists():
            raise DuplicateRelationshipTarget('team repository permission')
        grant.team = team
        grant.repository = repository
        grant.operation = permission
        grant.save(update_fields=['team', 'repository', 'operation'])
        return grant


def delete_team_repository_permission(actor, grant_id):
    """Delete one stored team-repository grant inside one organization."""
    grant_pk = _require_pk(grant_id, 'team repository permission')
    with transaction.atomic():
        actor = _locked_actor(actor)
        grant = _lock_grant(grant_pk)
        team = _lock_team(grant.team_id)
        repository = _lock_repository(grant.repository_id)
        team_organization, repository_organization = (
            _lock_team_and_repository_organizations(team, repository)
        )
        _require_manage(actor, [team_organization, repository_organization])
        _require_same_organization(
            team_organization, repository_organization,
        )
        grant.delete()


def delete_team(actor, team_id):
    """Delete the stored team. Does not move ``Team.organization``."""
    team_pk = _require_pk(team_id, 'team')
    with transaction.atomic():
        actor = _locked_actor(actor)
        team = _lock_team(team_pk)
        organization = _lock_parent_organization(team)
        _require_manage(actor, [organization])
        team.delete()


def _require_repository_name(name):
    field = Repository._meta.get_field('name')
    if (
        not isinstance(name, str)
        or name == ''
        or name != name.strip()
        or len(name) > field.max_length
    ):
        raise UndefinedRelationshipWrite('repository name')
    return name


def create_repository(actor, organization_id, name):
    """Create one repository inside a persisted organization.

    The locked organization is the authorization boundary. The insert
    follows ``manage_organization`` on that row. A later change to
    ``Repository.organization`` is still ``move_repository_organization``,
    which refuses.
    """
    organization_pk = _require_pk(organization_id, 'organization')
    repository_name = _require_repository_name(name)
    with transaction.atomic():
        actor = _locked_actor(actor)
        organization = _lock_organization(organization_pk)
        _require_manage(actor, [organization])
        if Repository.objects.filter(
            organization=organization, name=repository_name,
        ).exists():
            raise DuplicateRelationshipTarget('repository')
        return Repository.objects.create(
            organization=organization, name=repository_name,
        )


def delete_repository(actor, repository_id):
    """Delete the stored repository after authorizing its organization.

    Collaborator rows and team grants cascade inside this transaction,
    after ``manage_organization`` on the locked stored organization.
    Does not move ``Repository.organization``.
    """
    repository_pk = _require_pk(repository_id, 'repository')
    with transaction.atomic():
        actor = _locked_actor(actor)
        repository = _lock_repository(repository_pk)
        organization = _lock_parent_organization(repository)
        _require_manage(actor, [organization])
        repository.delete()


def _lock_ownership(ownership_id):
    return _locked_one(
        OrganizationOwnership,
        _require_pk(ownership_id, 'organization ownership'),
        'organization ownership',
    )


def _peek_ownership_organization_id(ownership_pk):
    """Return the organization id without locking the ownership row.

    The organization row is locked before the ownership row, matching
    ``delete_user``. A non-locking peek only chooses that organization.
    It is not the owner count.
    """
    organization_id = OrganizationOwnership.objects.filter(
        pk=ownership_pk,
    ).values_list('organization_id', flat=True).first()
    if organization_id is None:
        raise MissingRelationshipTarget(
            'organization ownership', ownership_pk,
        )
    return organization_id


def _current_remaining_owner_ids(
    organization, *, excluded_user_id=None, excluded_ownership_pk=None,
):
    """Lock this organization's ownership rows and return the survivors.

    Callers already hold ``organization`` via ``select_for_update``.
    This query is the current read: ``select_for_update`` locks the
    ownership rows in primary-key order and re-reads them. A plain
    ``exists()`` can still return a co-owner from a repeatable-read
    snapshot taken before that organization lock, after a supported
    write has removed the co-owner and committed.
    """
    rows = OrganizationOwnership.objects.select_for_update().filter(
        organization=organization,
    ).order_by('pk')
    if excluded_user_id is not None:
        rows = rows.exclude(user_id=excluded_user_id)
    if excluded_ownership_pk is not None:
        rows = rows.exclude(pk=excluded_ownership_pk)
    return list(rows.values_list('pk', flat=True))


def _refuse_unless_another_owner(organization, *, excluding_ownership_pk):
    """Require another owner when ``organization`` is conventional.

    The organization row is already locked in this transaction. The
    count is the locking read of the rows that would remain.
    ``register(condition=)`` cannot do this: at the pinned core it is
    compiled into the trust-row ``EXISTS`` and reads rows that already
    exist. It has no aggregate over owners that would remain, and core
    installs no save or delete signal.
    """
    if organization.personal_user_id is not None:
        return
    if not _current_remaining_owner_ids(
        organization, excluded_ownership_pk=excluding_ownership_pk,
    ):
        raise LastOrganizationOwner((organization.pk,))


def _refuse_ownerless_conventional_organizations(user):
    """Lock this user's conventional organizations in primary-key order.

    ``user`` is already ``select_for_update``d, so a new ownership row
    for that user waits on databases that share the row lock with the
    foreign-key insert. The personal organization is omitted because
    ``delete_user`` deletes it with the user. Which organizations to
    lock can come from an ordinary read. The decision, after each
    organization lock, is ``_current_remaining_owner_ids``. Every
    organization that would be left empty is reported together.
    """
    organization_ids = sorted(set(
        OrganizationOwnership.objects.filter(
            user=user,
            organization__personal_user__isnull=True,
        ).values_list('organization_id', flat=True)
    ))
    blocked = []
    for organization_id in organization_ids:
        organization = _lock_organization(organization_id)
        if organization.personal_user_id is not None:
            continue
        if not _current_remaining_owner_ids(
            organization, excluded_user_id=user.pk,
        ):
            blocked.append(organization.pk)
    if blocked:
        raise LastOrganizationOwner(tuple(blocked))


def update_organization_ownership(
    actor, ownership_id, *, user_id=None, organization_id=None,
):
    """Change the stored ownership row's user, organization, or both.

    The stored organization is locked and authorized before a
    replacement organization is loaded. That replacement is authorized
    on its own stored row. The ownership row being saved is not the
    inquiry. Reassigning the user leaves the row on the same
    organization, so the sole owner may be replaced. Moving the row
    off a conventional organization is refused when no other owner
    would remain. An active superuser skips only the inquiry.
    """
    if user_id is None and organization_id is None:
        raise UndefinedRelationshipWrite('organization ownership update')
    ownership_pk = _require_pk(ownership_id, 'organization ownership')
    user_pk = None if user_id is None else _require_pk(user_id, 'user')
    organization_pk = (
        None if organization_id is None
        else _require_pk(organization_id, 'organization')
    )
    with transaction.atomic():
        actor = _locked_actor(actor)
        stored_id = _peek_ownership_organization_id(ownership_pk)
        stored = _lock_organization(stored_id)
        _require_manage(actor, [stored])
        target = stored
        if organization_pk is not None and organization_pk != stored.pk:
            target = _lock_organization(organization_pk)
            _require_manage(actor, [target])
        ownership = _lock_ownership(ownership_pk)
        if ownership.organization_id != stored.pk:
            stored = _lock_organization(ownership.organization_id)
            _require_manage(actor, [stored])
            if target.pk == stored_id:
                target = stored
        if user_pk is None:
            user = _lock_user(ownership.user_id)
        else:
            user = _lock_user(user_pk)
        moved = (
            user.pk != ownership.user_id
            or target.pk != ownership.organization_id
        )
        if moved:
            conflict = OrganizationOwnership.objects.filter(
                user=user, organization=target,
            ).exclude(pk=ownership.pk)
            if conflict.exists():
                raise DuplicateRelationshipTarget('organization ownership')
        if target.pk != stored.pk:
            _refuse_unless_another_owner(
                stored, excluding_ownership_pk=ownership.pk,
            )
        if not moved:
            return ownership
        ownership.user = user
        ownership.organization = target
        ownership.save(update_fields=['user', 'organization'])
        return ownership


def delete_organization_ownership(actor, ownership_id):
    """Delete one stored ownership row.

    A conventional organization must still have another owner. An
    active superuser skips the inquiry and still hits that refusal.
    A personal organization is not conventional; ``delete_user`` is
    what removes it with the user.
    """
    ownership_pk = _require_pk(ownership_id, 'organization ownership')
    with transaction.atomic():
        actor = _locked_actor(actor)
        organization = _lock_organization(
            _peek_ownership_organization_id(ownership_pk),
        )
        _require_manage(actor, [organization])
        ownership = _lock_ownership(ownership_pk)
        if ownership.organization_id != organization.pk:
            organization = _lock_organization(ownership.organization_id)
            _require_manage(actor, [organization])
        _refuse_unless_another_owner(
            organization, excluding_ownership_pk=ownership.pk,
        )
        ownership.delete()


def _refuse_organization_boundary_move(
    actor, model, parent_id, parent_label, organization_id, boundary_label,
):
    """Resolve both organizations, then leave the stored boundary in place.

    Moving ``Team.organization`` re-scopes membership, the permission
    ceiling, and every team-repository grant. Moving
    ``Repository.organization`` re-scopes collaborator bundles and
    those same grants. A grant has to stay inside one organization.
    Authorizing ``manage_organization`` on the stored organization and
    the replacement would not decide whether the grants follow, are
    deleted, or block the move. Both organizations are locked in
    primary-key order so the refusal is about the persisted rows. An
    inactive actor is denied before the active-superuser bypass. The
    bypass is not applied: an active superuser receives the same
    refusal.
    """
    parent_pk = _require_pk(parent_id, parent_label)
    organization_pk = _require_pk(organization_id, 'organization')
    with transaction.atomic():
        actor = _locked_actor(actor)
        parent = _locked_one(model, parent_pk, parent_label)
        stored_id = parent.organization_id
        for pk in sorted({stored_id, organization_pk}):
            _lock_organization(pk)
        if not actor.is_active:
            raise ManagementDenied(stored_id)
        raise ImmutableOrganizationBoundary(boundary_label)


def move_team_organization(actor, team_id, organization_id):
    """Refuse to change the stored ``Team.organization``."""
    _refuse_organization_boundary_move(
        actor, Team, team_id, 'team', organization_id, 'Team',
    )


def move_repository_organization(actor, repository_id, organization_id):
    """Refuse to change the stored ``Repository.organization``."""
    _refuse_organization_boundary_move(
        actor,
        Repository,
        repository_id,
        'repository',
        organization_id,
        'Repository',
    )
