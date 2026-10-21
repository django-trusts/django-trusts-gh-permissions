"""Authorization backend for this app's catalog.

``TrustModelBackendMixin`` takes the recorder and catalog from the
``TrustsConfigMixin`` app config that contains this class. The
permission terminal is ``auth.Permission``.
"""

from django.contrib.auth.backends import BaseBackend

from trusts.backends import TrustModelBackendMixin


class GhAuthorizationBackend(TrustModelBackendMixin, BaseBackend):
    """Instance registry host. Not ``TrustModelBackend``.

    ``get_all_permissions`` and ``get_group_permissions`` are the mixin
    implementations. They enumerate real ``auth.Permission`` codenames.
    """
