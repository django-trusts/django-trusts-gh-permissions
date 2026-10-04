"""GH-shaped domain models. Authorization paths are registered in policy.

Public relations used to authorize:

    user.teams
    team.allowed_operations
    repository.organization
    collaborator.permissions

``organization.teams`` and ``organization.repositories`` are containment.
``Alias`` reserves the shared current name. Authorization does not
traverse it, and it stores no redirects or name history.
``OrganizationOwnership`` is the owner grant: one row per user and
organization, exposed as ``Organization.owners``. Ordinary organization
membership is not modeled. Team stays separate and does not carry
this bundle. These models never inherit Content and never expose
``.trusts``, ``.trustees``, ``.contexts``, ``.roles``, or ``.groups``.
"""

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import models

from trusts.query import AuthorizedManager, PermittedUsersMixin


class Alias(models.Model):
    """Shared current-name ledger for users and conventional organizations.

    ``User.username`` and ``Organization.name`` remain the real names.
    Nothing here points at either row, so authorization cannot traverse
    the ledger and the schema cannot keep the three columns identical.
    """

    name = models.CharField(max_length=150, unique=True)

    def __str__(self):
        return self.name


class Organization(models.Model):
    """Conventional named organization, or one user's personal organization.

    Conventional rows set ``name`` and leave ``personal_user`` null.
    Personal rows leave ``name`` null and set ``personal_user``. A
    personal organization's displayed name is that user's username.
    Every row points at the same seeded owner ``Group`` for now.
    """

    name = models.CharField(max_length=40, unique=True, null=True, blank=True)
    personal_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        related_name='personal_organization',
        on_delete=models.CASCADE,
    )
    owner_group = models.ForeignKey(
        Group,
        related_name='owner_organizations',
        on_delete=models.PROTECT,
    )
    owners = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='OrganizationOwnership',
        through_fields=('organization', 'user'),
        related_name='owned_organizations',
        blank=True,
    )

    objects = AuthorizedManager()

    class Meta:
        permissions = (
            ('manage_organization', 'Can manage organization'),
        )
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(name__isnull=False, personal_user__isnull=True)
                    & ~models.Q(name='')
                ) | models.Q(name__isnull=True, personal_user__isnull=False),
                name='organization_personal_or_conventional',
            ),
        )

    def clean(self):
        super().clean()
        named = bool(self.name)
        personal = self.personal_user_id is not None
        if named == personal:
            raise ValidationError(
                'An organization is conventional (a name, and no personal '
                'user) or personal (a personal user, and no name).'
            )

    @property
    def display_name(self):
        if self.personal_user_id is not None:
            return self.personal_user.username
        return self.name

    def __str__(self):
        return self.display_name or ''


class OrganizationOwnership(models.Model):
    """One owner row per user and organization.

    Every row is an owner. A user with no row is not an owner.
    Ordinary organization membership is a later relationship. Team
    stays separate.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='organization_ownerships',
        on_delete=models.CASCADE,
    )
    organization = models.ForeignKey(
        Organization,
        related_name='ownerships',
        on_delete=models.CASCADE,
    )

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=('user', 'organization'),
                name='unique_organization_ownership',
            ),
        )

    def __str__(self):
        return '%s @ %s' % (self.user_id, self.organization_id)


class Team(models.Model):
    """Collective subject. Membership reverse is ``user.teams``."""

    organization = models.ForeignKey(
        Organization, related_name='teams', on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=40)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, related_name='teams', blank=True,
    )
    allowed_operations = models.ManyToManyField(
        Permission, related_name='allowed_teams', blank=True,
    )

    class Meta:
        unique_together = ('organization', 'name')

    def __str__(self):
        return self.name


class Repository(PermittedUsersMixin, models.Model):
    """Protected resource. ``.authorized(user, operation)`` takes a Permission."""

    organization = models.ForeignKey(
        Organization, related_name='repositories', on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=40)

    objects = AuthorizedManager()

    class Meta:
        verbose_name = 'repository'
        verbose_name_plural = 'repositories'
        constraints = (
            models.UniqueConstraint(
                fields=('organization', 'name'),
                name='unique_repository_name_per_organization',
            ),
        )
        permissions = (
            ('read_repository', 'Can read repository'),
            ('write_repository', 'Can write repository'),
            ('admin_repository', 'Can administer repository'),
        )

    def __str__(self):
        return self.name


class RepositoryCollaborator(models.Model):
    """Direct user access to one repository.

    One row per user and repository. ``permissions`` is the selected
    bundle. Organization membership is not required.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='repository_collaborations',
        on_delete=models.CASCADE,
    )
    repository = models.ForeignKey(
        Repository,
        related_name='collaborators',
        on_delete=models.CASCADE,
    )
    permissions = models.ManyToManyField(
        Permission,
        related_name='repository_collaborators',
        blank=True,
    )

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=('user', 'repository'),
                name='unique_repository_collaborator',
            ),
        )

    def __str__(self):
        return '%s @ %s' % (self.user_id, self.repository_id)


class TeamRepositoryPermission(models.Model):
    """Team → repository permission. Alignment compares team and repository orgs."""

    team = models.ForeignKey(
        Team, related_name='repository_permissions', on_delete=models.CASCADE,
    )
    repository = models.ForeignKey(
        Repository, related_name='team_permissions', on_delete=models.CASCADE,
    )
    operation = models.ForeignKey(
        Permission,
        related_name='team_repository_permissions',
        on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ('team', 'repository', 'operation')

    def clean(self):
        """Keep a team grant inside one organization.

        Admin ``ModelForm`` calls this. ``QuerySet.create`` does not, so
        a misaligned row can still be stored for fail-closed tests.
        """
        super().clean()
        if self.team_id and self.repository_id:
            if self.team.organization_id != self.repository.organization_id:
                raise ValidationError(
                    'Repository grants must stay inside one organization.'
                )
