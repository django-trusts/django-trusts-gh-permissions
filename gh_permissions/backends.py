"""Mixin-only Trusts handle so GH can contribute registrations.

Not ``TrustModelBackend``: GH does not use the historical TrustGroup
compiler. The mixin default is ``PlanQueryCompiler``. The permission
terminal is ``auth.Permission``, so string enumeration stays on the
mixin ``_perm_codes`` path.
"""

from django.contrib.auth.backends import BaseBackend

from trusts.backends import TrustModelBackendMixin


class GhAuthorizationBackend(TrustModelBackendMixin, BaseBackend):
    """Instance registry host. Not ``TrustModelBackend``.

    ``get_all_permissions`` and ``get_group_permissions`` are the mixin
    implementations. They enumerate real ``auth.Permission`` codenames.
    """
