"""Development-only example authorization graph.

Users and the conventional organization are created through
``gh_permissions.services``, so an alias, a personal organization, and
an ownership row stay together. The organization name is the seed
identity. An exact match is reused. Anything missing, partial, or
different for that name fails before this command writes. Passwords
are development-only and are not for production.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.db.models import Q

from gh_permissions.models import (
    Alias,
    Organization,
    OrganizationOwnership,
    Repository,
    RepositoryCollaborator,
    Team,
    TeamRepositoryPermission,
)
from gh_permissions.services import (
    OWNER_GROUP_NAME,
    OWNER_PERMISSIONS,
    AliasConflict,
    create_organization,
    create_user,
    name_is_taken,
)


DEFAULT_ORGANIZATION_NAME = 'Example Organization'
TEAM_NAME = 'readers'
REPOSITORY_TITLE = 'shared-repo'

SUPERUSER_USERNAME = 'example-superuser'
OWNER_USERNAME = 'example-owner'
DIRECT_USERNAME = 'example-direct'
TEAM_USERNAME = 'example-team'
OUTSIDER_USERNAME = 'example-outsider'

# Documented in the README. Development-only. Not a production credential.
USER_SPECS = (
    {
        'username': SUPERUSER_USERNAME,
        'password': 'example-superuser-dev-only',
        'is_superuser': True,
        'is_staff': True,
        'proves': (
            'application superuser; authorized lists persisted grants only'
        ),
    },
    {
        'username': OWNER_USERNAME,
        'password': 'example-owner-dev-only',
        'is_superuser': False,
        'is_staff': True,
        'proves': (
            'staff organization owner; OrganizationOwnership grants the '
            'seeded owner group (manage_organization, read_repository, '
            'write_repository, admin_repository) on the organization and '
            'its repositories; Django model permissions for those rows'
        ),
    },
    {
        'username': DIRECT_USERNAME,
        'password': 'example-direct-dev-only',
        'is_superuser': False,
        'is_staff': False,
        'proves': (
            'RepositoryCollaborator read_repository and write_repository '
            'on %s' % REPOSITORY_TITLE
        ),
    },
    {
        'username': TEAM_USERNAME,
        'password': 'example-team-dev-only',
        'is_superuser': False,
        'is_staff': False,
        'proves': (
            'member of %s; read_repository on %s through team membership, '
            'the operation ceiling, and the team grant'
            % (TEAM_NAME, REPOSITORY_TITLE)
        ),
    },
    {
        'username': OUTSIDER_USERNAME,
        'password': 'example-outsider-dev-only',
        'is_superuser': False,
        'is_staff': False,
        'proves': 'no repository access on %s' % REPOSITORY_TITLE,
    },
)

DEVELOPMENT_PASSWORDS = {
    spec['username']: spec['password'] for spec in USER_SPECS
}

DIRECT_CODENAMES = ('read_repository', 'write_repository')
CEILING_CODENAMES = ('read_repository',)
TEAM_GRANT_CODENAMES = ('read_repository', 'write_repository')
EXCLUDED_CODENAME = 'write_repository'
OWNER_ACTIONS = ('view', 'add', 'change', 'delete')
SCOPED_MODELS = (
    Organization,
    Team,
    Repository,
    RepositoryCollaborator,
    TeamRepositoryPermission,
    OrganizationOwnership,
)

DEVELOPMENT_ONLY_NOTICE = (
    'Development-only example seed. These passwords are not for production.'
)


def _owner_permission_keys():
    keys = []
    for model in SCOPED_MODELS:
        for action in OWNER_ACTIONS:
            codename = '%s_%s' % (action, model._meta.model_name)
            keys.append((model._meta.app_label, model._meta.model_name, codename))
    return tuple(keys)


def _natural(permission):
    content_type = permission.content_type
    return (content_type.app_label, content_type.model, permission.codename)


class Command(BaseCommand):
    """Seed one example organization without touching unrelated rows."""

    help = (
        'Seed a development-only example authorization graph. '
        'Passwords are development-only and are not for production.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--organization-name',
            default=DEFAULT_ORGANIZATION_NAME,
            help=(
                'Organization.name identity for this seed. '
                'Defaults to "%s". Validated before any rows are written.'
                % DEFAULT_ORGANIZATION_NAME
            ),
        )

    def handle(self, *args, **options):
        if (
            EXCLUDED_CODENAME in CEILING_CODENAMES
            or EXCLUDED_CODENAME not in TEAM_GRANT_CODENAMES
        ):
            raise CommandError(
                'Seed definition does not show a team-ceiling exclusion.'
            )
        organization_name = _validate_organization_name(
            options['organization_name'],
        )
        repository_permissions, owner_permissions = _load_permissions()
        organization = Organization.objects.filter(
            name=organization_name, personal_user__isnull=True,
        ).first()
        if organization is None:
            _ensure_names_available(organization_name)
            try:
                with transaction.atomic():
                    _create_graph(
                        organization_name,
                        repository_permissions,
                        owner_permissions,
                    )
            except (IntegrityError, AliasConflict) as exc:
                raise CommandError(
                    'Example seed could not be created: %s' % exc
                )
            self._report('created', organization_name)
            return

        problems = _graph_problems(organization, owner_permissions)
        if problems:
            raise CommandError(
                'Organization %r does not match the example seed '
                '(missing, partial, or different graph). '
                'Refusing to change existing rows:\n%s'
                % (
                    organization_name,
                    '\n'.join('- %s' % item for item in problems),
                )
            )
        self._report('reused', organization_name)

    def _report(self, status, organization_name):
        self.stdout.write(DEVELOPMENT_ONLY_NOTICE)
        self.stdout.write('Status: %s' % status)
        self.stdout.write('Organization: %s' % organization_name)
        self.stdout.write(
            'Users and this organization are created through '
            'gh_permissions.services. Each user reserves an alias and a '
            'personal organization.'
        )
        for spec in USER_SPECS:
            self.stdout.write(
                'User %s: %s; development-only password %s'
                % (spec['username'], spec['proves'], spec['password'])
            )
        self.stdout.write(
            'Team %s: ceiling %s; member %s'
            % (TEAM_NAME, ', '.join(CEILING_CODENAMES), TEAM_USERNAME)
        )
        self.stdout.write(
            'Repository %s: direct RepositoryCollaborator %s (%s); '
            'team path %s (%s)'
            % (
                REPOSITORY_TITLE,
                DIRECT_USERNAME,
                ', '.join(DIRECT_CODENAMES),
                TEAM_NAME,
                ', '.join(TEAM_GRANT_CODENAMES),
            )
        )
        self.stdout.write(
            'Excluded by the team ceiling: %s on %s is granted to %s '
            'and is not an allowed operation.'
            % (EXCLUDED_CODENAME, REPOSITORY_TITLE, TEAM_NAME)
        )


def _validate_organization_name(raw):
    """Conventional ``Organization.name`` before any rows are written.

    Personal organizations store a null name. A seed name has to be a
    non-blank conventional name within the field's max length.
    """
    if not isinstance(raw, str) or raw == '' or raw != raw.strip():
        raise CommandError(
            'Organization name %r is not valid for Organization.name: '
            'a conventional name must be a non-blank string.' % (raw,)
        )
    field = Organization._meta.get_field('name')
    try:
        return field.clean(raw, None)
    except ValidationError as exc:
        raise CommandError(
            'Organization name %r is not valid for Organization.name: %s'
            % (raw, '; '.join(exc.messages))
        )


def _get_permission(app_label, model, codename):
    identity = '%s.%s.%s' % (app_label, model, codename)
    try:
        return Permission.objects.get(
            content_type__app_label=app_label,
            content_type__model=model,
            codename=codename,
        )
    except Permission.DoesNotExist:
        raise CommandError(
            'Missing auth.Permission %s. Run migrations before seed_example.'
            % identity
        )
    except Permission.MultipleObjectsReturned:
        raise CommandError(
            'Multiple auth.Permission rows for %s. '
            'Refusing to guess a primary key.' % identity
        )


def _load_permissions():
    repository_permissions = {
        codename: _get_permission('gh_permissions', 'repository', codename)
        for codename in DIRECT_CODENAMES
    }
    owner_permissions = {
        key: _get_permission(*key) for key in _owner_permission_keys()
    }
    return repository_permissions, owner_permissions


def _ensure_names_available(organization_name):
    User = get_user_model()
    usernames = [spec['username'] for spec in USER_SPECS]
    existing = list(
        User.objects.filter(username__in=usernames)
        .order_by('username')
        .values_list('username', flat=True)
    )
    if existing:
        raise CommandError(
            'Organization %r is not in the database, but these users '
            'already exist: %s. Refusing to modify unrelated rows.'
            % (organization_name, ', '.join(existing))
        )
    reserved = [
        name for name in usernames if name_is_taken(name)
    ]
    if name_is_taken(organization_name):
        reserved.append(organization_name)
    if reserved:
        raise CommandError(
            'Organization %r is not in the database, but these names '
            'are already reserved: %s. Refusing to modify unrelated rows.'
            % (organization_name, ', '.join(reserved))
        )


def _create_graph(organization_name, repository_permissions, owner_permissions):
    users = {}
    for spec in USER_SPECS:
        users[spec['username']] = create_user(
            spec['username'],
            password=spec['password'],
            email='',
            is_staff=spec['is_staff'],
            is_superuser=spec['is_superuser'],
            is_active=True,
        )
    users[OWNER_USERNAME].user_permissions.set(owner_permissions.values())
    organization = create_organization(organization_name)
    OrganizationOwnership.objects.create(
        user=users[OWNER_USERNAME],
        organization=organization,
    )
    team = Team.objects.create(organization=organization, name=TEAM_NAME)
    team.members.set([users[TEAM_USERNAME]])
    team.allowed_operations.set([
        repository_permissions[codename] for codename in CEILING_CODENAMES
    ])
    repository = Repository.objects.create(
        organization=organization, name=REPOSITORY_TITLE,
    )
    collaboration = RepositoryCollaborator.objects.create(
        user=users[DIRECT_USERNAME],
        repository=repository,
    )
    collaboration.permissions.set([
        repository_permissions[codename] for codename in DIRECT_CODENAMES
    ])
    for codename in TEAM_GRANT_CODENAMES:
        TeamRepositoryPermission.objects.create(
            team=team,
            repository=repository,
            operation=repository_permissions[codename],
        )


def _graph_problems(organization, owner_permissions):
    problems = []
    try:
        owner_group = Group.objects.get(name=OWNER_GROUP_NAME)
    except Group.DoesNotExist:
        owner_group = None
        problems.append('missing owner group %s' % OWNER_GROUP_NAME)
    problems.extend(_user_problems(organization, owner_permissions, owner_group))
    _organization_problems(organization, owner_group, problems)
    _team_problems(organization, problems)
    _repository_problems(organization, problems)
    _collaborator_problems(organization, problems)
    _team_grant_problems(organization, problems)
    return problems


def _user_problems(organization, owner_permissions, owner_group):
    User = get_user_model()
    problems = []
    expected_owner_keys = set(owner_permissions)
    for spec in USER_SPECS:
        username = spec['username']
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            problems.append('missing user %s' % username)
            continue
        if user.is_superuser != spec['is_superuser']:
            problems.append('user %s superuser flag differs' % username)
        if user.is_staff != spec['is_staff']:
            problems.append('user %s staff flag differs' % username)
        if not user.is_active:
            problems.append('user %s is inactive' % username)
        if user.email or user.first_name or user.last_name:
            problems.append(
                'user %s has profile fields the seed leaves blank' % username
            )
        if user.groups.exists():
            problems.append('user %s belongs to groups' % username)
        if not check_password(spec['password'], user.password):
            problems.append(
                'user %s password does not match the development-only '
                'seed password' % username
            )
        if not Alias.objects.filter(name=username).exists():
            problems.append('missing alias for user %s' % username)
        actual = set(user.user_permissions.values_list(
            'content_type__app_label',
            'content_type__model',
            'codename',
        ))
        expected = expected_owner_keys if username == OWNER_USERNAME else set()
        missing = expected - actual
        extra = actual - expected
        if missing or extra:
            problems.append(
                'user %s Django permissions missing %s extra %s'
                % (username, sorted(missing), sorted(extra))
            )
        personal = _personal_organization(user, owner_group, problems)
        owned = set(
            user.organization_ownerships.values_list('organization_id', flat=True)
        )
        expected_owned = set()
        if personal is not None:
            expected_owned.add(personal.pk)
        if username == OWNER_USERNAME:
            expected_owned.add(organization.pk)
        if owned != expected_owned:
            problems.append(
                'user %s ownership rows differ from the personal '
                'organization and, for the owner, this organization'
                % username
            )
    return problems


def _personal_organization(user, owner_group, problems):
    try:
        personal = user.personal_organization
    except Organization.DoesNotExist:
        problems.append('missing personal organization for %s' % user.username)
        return None
    if personal.name is not None or personal.personal_user_id != user.pk:
        problems.append(
            'personal organization for %s is not a personal row' % user.username
        )
    if owner_group is not None and personal.owner_group_id != owner_group.pk:
        problems.append(
            'personal organization for %s does not use %s'
            % (user.username, OWNER_GROUP_NAME)
        )
    if personal.teams.exists() or personal.repositories.exists():
        problems.append(
            'personal organization for %s has teams or repositories'
            % user.username
        )
    owners = set(personal.owners.values_list('username', flat=True))
    if owners != {user.username}:
        problems.append(
            'personal organization for %s owners are %s'
            % (user.username, sorted(owners))
        )
    return personal


def _organization_problems(organization, owner_group, problems):
    if organization.personal_user_id is not None:
        problems.append('organization is personal, expected a conventional name')
    if not Alias.objects.filter(name=organization.name).exists():
        problems.append('missing alias for organization %s' % organization.name)
    if owner_group is None:
        return
    if organization.owner_group_id != owner_group.pk:
        problems.append(
            'organization owner group is not %s' % OWNER_GROUP_NAME
        )
    actual = set(owner_group.permissions.values_list(
        'content_type__app_label',
        'content_type__model',
        'codename',
    ))
    expected = {
        ('gh_permissions', model_name, codename)
        for model_name, codename in OWNER_PERMISSIONS
    }
    if actual != expected:
        problems.append(
            'owner group permissions are %s, expected %s'
            % (sorted(actual), sorted(expected))
        )
    owners = set(organization.owners.values_list('username', flat=True))
    if owners != {OWNER_USERNAME}:
        problems.append(
            'organization owners are %s, expected [%s]'
            % (sorted(owners), OWNER_USERNAME)
        )


def _team_problems(organization, problems):
    names = list(organization.teams.order_by('name').values_list('name', flat=True))
    if names != [TEAM_NAME]:
        problems.append('teams are %s, expected [%s]' % (names, TEAM_NAME))
    team = organization.teams.filter(name=TEAM_NAME).first()
    if team is None:
        return
    members = set(team.members.values_list('username', flat=True))
    if members != {TEAM_USERNAME}:
        problems.append(
            'team %s members are %s, expected [%s]'
            % (TEAM_NAME, sorted(members), TEAM_USERNAME)
        )
    actual_ops = set(team.allowed_operations.values_list(
        'content_type__app_label',
        'content_type__model',
        'codename',
    ))
    expected_ops = {
        ('gh_permissions', 'repository', codename)
        for codename in CEILING_CODENAMES
    }
    if actual_ops != expected_ops:
        problems.append(
            'team %s ceiling is %s, expected %s'
            % (TEAM_NAME, sorted(actual_ops), sorted(expected_ops))
        )


def _repository_problems(organization, problems):
    names = list(
        organization.repositories.order_by('name').values_list('name', flat=True)
    )
    if names != [REPOSITORY_TITLE]:
        problems.append(
            'repositories are %s, expected [%s]' % (names, REPOSITORY_TITLE)
        )


def _collaborator_problems(organization, problems):
    rows = RepositoryCollaborator.objects.filter(repository__organization=organization)
    actual = set()
    for row in rows:
        permissions = tuple(sorted(row.permissions.values_list(
            'content_type__app_label',
            'content_type__model',
            'codename',
        )))
        actual.add((row.user.username, row.repository.name, permissions))
    expected_permissions = tuple(sorted(
        ('gh_permissions', 'repository', codename)
        for codename in DIRECT_CODENAMES
    ))
    expected = {(DIRECT_USERNAME, REPOSITORY_TITLE, expected_permissions)}
    if actual != expected:
        problems.append(
            'repository collaborators are %s, expected %s'
            % (sorted(actual), sorted(expected))
        )


def _team_grant_problems(organization, problems):
    actual = set(
        TeamRepositoryPermission.objects.filter(
            Q(repository__organization=organization)
            | Q(team__organization=organization)
        ).values_list(
            'team__name',
            'repository__name',
            'team__organization__name',
            'repository__organization__name',
            'operation__content_type__app_label',
            'operation__content_type__model',
            'operation__codename',
        )
    )
    expected = {
        (
            TEAM_NAME,
            REPOSITORY_TITLE,
            organization.name,
            organization.name,
            'gh_permissions',
            'repository',
            codename,
        )
        for codename in TEAM_GRANT_CODENAMES
    }
    if actual != expected:
        problems.append(
            'team grants are %s, expected %s'
            % (sorted(actual), sorted(expected))
        )
