"""Authorization backend for this app's catalog.

``TrustModelBackendMixin`` reads the catalog from the
``TrustsConfigMixin`` app config that contains this class. Relations
are recorded on that config, not on the backend. The permission
terminal is ``auth.Permission``.
"""

from django.contrib.auth.backends import BaseBackend

from trusts.backends import TrustModelBackendMixin


class GhAuthorizationBackend(TrustModelBackendMixin, BaseBackend):
    """Object-permission backend for this app's catalog. Not ``TrustModelBackend``.

    ``get_all_permissions`` and ``get_group_permissions`` are the mixin
    implementations. They enumerate real ``auth.Permission`` codenames.
    """
