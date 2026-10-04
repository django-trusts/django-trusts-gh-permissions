"""GH-shaped domain models. Authorization paths are registered in policy.

Public relations used to authorize:

    user.teams
    team.allowed_operations
    repository.organization

``organization.teams`` / ``organization.repositories`` are owner
containment. They are not a grant. Organization membership is not
modeled here and is not a Trusts grant edge. These models never inherit
Content and never expose ``.trusts``, ``.trustees``, ``.contexts``,
``.roles``, or ``.groups``.
"""

from django.conf import settings
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import models

from trusts.query import AuthorizedManager, PermittedUsersMixin


class Organization(models.Model):
    """Containment. Not a membership roster and not a repository grant.

    Administration is ``OrganizationOwnerPermission`` for the
    ``manage_organization`` permission. No such row means unowned.
    """

    name = models.CharField(max_length=40, unique=True)

    objects = AuthorizedManager()

    class Meta:
        permissions = (
            ('manage_organization', 'Can manage organization'),
        )

    def __str__(self):
        return self.name


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
    title = models.CharField(max_length=40)

    objects = AuthorizedManager()

    class Meta:
        verbose_name = 'repository'
        verbose_name_plural = 'repositories'
        unique_together = ('organization', 'title')
        permissions = (
            ('read_repository', 'Can read repository'),
            ('write_repository', 'Can write repository'),
            ('admin_repository', 'Can administer repository'),
        )

    def __str__(self):
        return self.title


class UserRepositoryPermission(models.Model):
    """Direct user → repository permission. Three direct FKs."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='repository_permissions',
        on_delete=models.CASCADE,
    )
    repository = models.ForeignKey(
        Repository, related_name='user_permissions', on_delete=models.CASCADE,
    )
    operation = models.ForeignKey(
        Permission,
        related_name='user_repository_permissions',
        on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ('user', 'repository', 'operation')


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


class OrganizationOwnerPermission(models.Model):
    """Organization administration grant. Not a repository permission.

    ``operation`` is an ``auth.Permission`` scoped to ``Organization``,
    normally ``manage_organization``. No row means unowned. Deleting
    the user or the organization deletes the grant and leaves the other
    side in place.
    """

    organization = models.OneToOneField(
        Organization,
        related_name='owner_grant',
        on_delete=models.CASCADE,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='organization_owner_grants',
        on_delete=models.CASCADE,
    )
    operation = models.ForeignKey(
        Permission,
        related_name='organization_owner_grants',
        on_delete=models.CASCADE,
    )

    def __str__(self):
        return '%s:%s' % (self.organization, self.operation_id)
