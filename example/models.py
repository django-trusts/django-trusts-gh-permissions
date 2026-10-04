"""Application-owned user for the example/test project.

The gh_permissions library keeps swappable ``AUTH_USER_MODEL`` foreign
keys and does not import this model. Stock ``auth.User`` has no
``permitted`` method. This manager is how the example calls
``User.objects.permitted(content, perm)``.
"""

from django.contrib.auth.models import AbstractUser, UserManager

from trusts.query import PermittedUsersManagerMixin


class ExampleUserManager(PermittedUsersManagerMixin, UserManager):
    """Default manager for the example user. Does not replace the queryset class."""


class User(AbstractUser):
    objects = ExampleUserManager()
