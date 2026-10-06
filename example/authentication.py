"""Bearer authentication for the runnable repository API.

The token is not a row and not a literal. ``settings.EXAMPLE_API_TOKENS``
maps a token to a username. ``tests.settings`` fills that mapping from
environment variables when the process starts. An unset variable adds
no entry.

A header that does not use this keyword is ignored so session
authentication can still run. A bearer header that matches no
configured token is an authentication failure.
"""

import hashlib
import hmac

from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from trusts.query import is_active_principal


class BearerTokenAuthentication(BaseAuthentication):
    """``Authorization: Bearer <token>`` against ``EXAMPLE_API_TOKENS``.

    Every configured token is compared. ``hmac.compare_digest`` runs on
    SHA-256 digests, so the check does not stop at the first match and
    unequal lengths do not raise. An inactive principal is rejected
    with ``is_active_principal``, the same rule ``has_perm`` uses.
    """

    keyword = 'Bearer'

    def authenticate(self, request):
        auth = get_authorization_header(request).split()
        if not auth or auth[0].lower() != self.keyword.lower().encode():
            return None
        if len(auth) == 1:
            raise AuthenticationFailed(
                'Invalid token header. No credentials provided.',
            )
        if len(auth) > 2:
            raise AuthenticationFailed(
                'Invalid token header. Token string should not contain spaces.',
            )
        try:
            presented = auth[1].decode()
        except UnicodeError:
            raise AuthenticationFailed(
                'Invalid token header. Token string should not contain invalid characters.',
            )
        return self.authenticate_credentials(presented)

    def authenticate_credentials(self, presented):
        username = _username_for_token(presented)
        if username is None:
            raise AuthenticationFailed('Invalid token.')
        user_model = get_user_model()
        try:
            user = user_model.objects.get(**{
                user_model.USERNAME_FIELD: username,
            })
        except user_model.DoesNotExist:
            raise AuthenticationFailed('Invalid token.')
        if not is_active_principal(user):
            raise AuthenticationFailed('User inactive or deleted.')
        # None, not the presented secret. request.auth keeps this value
        # for the rest of the request.
        return (user, None)

    def authenticate_header(self, request):
        return self.keyword


def _digest(value):
    if isinstance(value, bytes):
        raw = value
    else:
        raw = str(value).encode('utf-8')
    return hashlib.sha256(raw).digest()


def _username_for_token(presented):
    tokens = getattr(settings, 'EXAMPLE_API_TOKENS', {})
    if not isinstance(tokens, dict):
        return None
    presented_digest = _digest(presented)
    matched = None
    for candidate, username in tokens.items():
        if hmac.compare_digest(_digest(candidate), presented_digest):
            matched = username
    return matched
