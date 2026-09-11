"""Mixin-only Trusts handle so GH can contribute registrations.

Not ``TrustModelBackend``: GH does not use Django permission strings or
the historical TrustGroup compiler. The mixin default is
``PlanQueryCompiler``.
"""

from django.contrib.auth.backends import BaseBackend

from trusts.backends import TrustModelBackendMixin


class GhAuthorizationBackend(TrustModelBackendMixin, BaseBackend):
    """Instance-only registry host. Not ``TrustModelBackend``."""
