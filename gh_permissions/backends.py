"""Mixin-only Trusts handle so GH can contribute registrations.

Not ``TrustModelBackend``: GH does not use Django permission strings or
the historical TrustGroup compiler. The mixin default is
``PlanQueryCompiler``.
"""

from django.contrib.auth.backends import BaseBackend

from trusts.backends import TrustModelBackendMixin


class GhAuthorizationBackend(TrustModelBackendMixin, BaseBackend):
    """Instance-only registry host. Not ``TrustModelBackend``.

    Django permission-string enumeration is not a GH surface.
    ``Operation`` is not ``auth.Permission``, so these methods opt out
    instead of inheriting ``_perm_codes()``.
    """

    def get_all_permissions(self, user_obj, obj=None):
        return set()

    def get_group_permissions(self, user_obj, obj=None):
        return set()
