"""GH-shaped domain models. Authorization paths are registered in policy.

Public relations used to authorize:

    team.members
    team.allowed_operations
    team.organization
    repository.organization

Organization membership is not a persisted grant edge. These models
never inherit Content and never expose ``.trusts``, ``.trustees``,
``.contexts``, ``.roles``, or ``.groups``.
"""

from django.conf import settings
from django.db import models

from trusts.query import AuthorizedManager


class Organization(models.Model):
    """Owner / containment. Membership is not modeled as a grant."""

    name = models.CharField(max_length=40, unique=True)

    def __str__(self):
        return self.name


class Operation(models.Model):
    """Permission value. Callers pass instances, never Django codenames."""

    code = models.CharField(max_length=40, unique=True)

    def __str__(self):
        return self.code


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
        Operation, related_name='teams', blank=True,
    )

    class Meta:
        unique_together = ('organization', 'name')

    def __str__(self):
        return self.name


class Repository(models.Model):
    """Protected resource. Instance-only ``.authorized(user, operation)``."""

    organization = models.ForeignKey(
        Organization, related_name='repositories', on_delete=models.CASCADE,
    )
    title = models.CharField(max_length=40)

    objects = AuthorizedManager()

    class Meta:
        verbose_name = 'repository'
        verbose_name_plural = 'repositories'
        unique_together = ('organization', 'title')

    def __str__(self):
        return self.title


class TeamRepositoryPermission(models.Model):
    """Team → repository permission. Alignment compares team and repository orgs."""

    team = models.ForeignKey(
        Team, related_name='repository_permissions', on_delete=models.CASCADE,
    )
    repository = models.ForeignKey(
        Repository, related_name='team_permissions', on_delete=models.CASCADE,
    )
    operation = models.ForeignKey(
        Operation, related_name='team_permissions', on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ('team', 'repository', 'operation')


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
        Operation, related_name='user_permissions', on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ('user', 'repository', 'operation')
