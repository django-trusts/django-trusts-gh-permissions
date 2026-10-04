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
from django.db import models

from trusts.query import AuthorizedManager, PermittedUsersMixin


class Organization(models.Model):
    """Owner / containment. Not a grant and not a membership roster."""

    name = models.CharField(max_length=40, unique=True)

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
