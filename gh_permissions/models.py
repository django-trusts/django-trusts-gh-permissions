"""GH-shaped domain models. Authorization paths are registered in policy.

Public relations used to authorize:

    account.teams
    account.organizations
    team.permission_bundles
    repository.organization

``organization.teams`` / ``organization.repositories`` are owner
containment. They are not a grant. These models never inherit Content
and never expose ``.trusts``, ``.trustees``, ``.contexts``, ``.roles``,
or ``.groups``.
"""

from django.db import models

from trusts.query import AuthorizedManager


class Account(models.Model):
    """Requester / person. Never the organization."""

    name = models.CharField(max_length=40, unique=True)

    def __str__(self):
        return self.name


class Organization(models.Model):
    """Owner / containment. Membership alone is not a grant."""

    name = models.CharField(max_length=40, unique=True)
    members = models.ManyToManyField(
        Account, related_name='organizations', blank=True,
    )

    def __str__(self):
        return self.name


class Team(models.Model):
    """Collective subject. Membership reverse is ``account.teams``."""

    organization = models.ForeignKey(
        Organization, related_name='teams', on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=40)
    members = models.ManyToManyField(
        Account, related_name='teams', blank=True,
    )

    class Meta:
        unique_together = ('organization', 'name')

    def __str__(self):
        return self.name


class Operation(models.Model):
    """Permission value. Resolution stays ``Operation.objects.get(code=...)``."""

    code = models.CharField(max_length=40, unique=True)

    def __str__(self):
        return self.code


class PermissionBundle(models.Model):
    """Operation ceiling for a team. Constraints never create a grant."""

    team = models.ForeignKey(
        Team, related_name='permission_bundles', on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=40)
    operations = models.ManyToManyField(
        Operation, related_name='bundles', blank=True,
    )

    class Meta:
        unique_together = ('team', 'name')

    def __str__(self):
        return self.name


class Repository(models.Model):
    """Protected resource. Instance-only ``AuthorizedManager`` surface."""

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


class TeamRepoGrant(models.Model):
    """Team → repository grant. Alignment compares team and repository orgs."""

    team = models.ForeignKey(
        Team, related_name='repo_grants', on_delete=models.CASCADE,
    )
    repository = models.ForeignKey(
        Repository, related_name='team_grants', on_delete=models.CASCADE,
    )
    operation = models.ForeignKey(
        Operation, related_name='team_grants', on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ('team', 'repository', 'operation')


class AccountRepoGrant(models.Model):
    """Direct account → repository grant. No extra condition."""

    account = models.ForeignKey(
        Account, related_name='repo_grants', on_delete=models.CASCADE,
    )
    repository = models.ForeignKey(
        Repository, related_name='direct_grants', on_delete=models.CASCADE,
    )
    operation = models.ForeignKey(
        Operation, related_name='direct_grants', on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ('account', 'repository', 'operation')
