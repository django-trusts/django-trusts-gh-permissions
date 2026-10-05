"""MCP authorization entry for the runnable example.

``django-oauth-toolkit`` validates the authorization request, sends an
anonymous human to Django's login, and issues an authorization code
when that human approves. This module owns the consent screen and the
example MCP HTTP endpoint so the package can be replaced.

The picker does not save an account or a repository. The access token
is only a test credential for that endpoint. Cancel still returns
``access_denied`` and stores nothing.

The toolkit's own ``AuthorizationView.get`` can skip the screen and
mint a code when ``skip_authorization`` or ``approval_prompt=auto``
says so. This view does not call that path.
"""

import base64
import hashlib
import importlib.metadata
import json
from urllib.parse import urlencode

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.exceptions import OAuthToolkitError
from oauth2_provider.models import get_application_model
from oauth2_provider.oauth2_backends import OAuthLibCore
from oauth2_provider.oauth2_validators import OAuth2Validator, is_valid_resource_uri
from oauth2_provider.scopes import get_scopes_backend
from oauth2_provider.settings import oauth2_settings
from oauth2_provider.views import AuthorizationView
from oauth2_provider.www_authenticate import build_bearer_challenge, challenge_status
from oauthlib.oauth2 import Server
from oauthlib.oauth2.rfc6749.errors import CustomOAuth2Error


OAUTH_TOOLKIT_DISTRIBUTION = 'django-oauth-toolkit'
OAUTH_TOOLKIT_VERSION = '3.4.1'

MCP_CLIENT_ID = 'example-mcp-client'
MCP_CLIENT_NAME = 'Example MCP client'
LOCAL_REDIRECT_URI = 'http://localhost:8000/mcp/callback/'
TEST_REDIRECT_URI = 'http://testserver/mcp/callback/'
TEST_MCP_RESOURCE = 'http://testserver/mcp'
# Cursor's documented static-client callbacks. The desktop app uses
# port 8787. The web and agents surface uses the https callback.
CURSOR_DESKTOP_REDIRECT_URI = 'http://localhost:8787/callback'
CURSOR_LOOPBACK_IP_REDIRECT_URI = 'http://127.0.0.1:8787/callback'
CURSOR_WEB_REDIRECT_URI = 'https://www.cursor.com/agents/mcp/oauth/callback'
REDIRECT_URIS = (
    LOCAL_REDIRECT_URI,
    TEST_REDIRECT_URI,
    CURSOR_DESKTOP_REDIRECT_URI,
    CURSOR_LOOPBACK_IP_REDIRECT_URI,
    CURSOR_WEB_REDIRECT_URI,
)
# Printed by seed_mcp_authorization and used as the default resource on
# that printed authorize URL. Authorize and /mcp do not read it.
# Discovery and the token audience check use the request host, so a
# deployment on another host works when the client sends that host's
# /mcp resource. A token for this localhost URL is a different audience.
LOCAL_MCP_URL = 'http://localhost:8000/mcp'
MCP_RESOURCE = LOCAL_MCP_URL
MCP_PROTOCOL_VERSION = '2025-03-26'
MCP_SERVER_NAME = 'example-mcp'
REQUESTED_SCOPES = ('read_repository', 'write_repository')
# Development-only PKCE verifier so the printed authorize URL is stable.
# Not a credential and not a grant.
DEV_CODE_VERIFIER = 'mcp-spike-dev-verifier-not-a-secret-0123456789'
DEV_STATE = 'mcp-spike'
# Never written into the debug dump. The authorization code on the
# callback is the value under inspection, so it is not in this set.
_HANDSHAKE_SECRET_KEYS = frozenset({
    'access_token',
    'client_secret',
    'code_verifier',
    'password',
    'refresh_token',
})


def installed_oauth_toolkit_version():
    return importlib.metadata.version(OAUTH_TOOLKIT_DISTRIBUTION)


def handshake_dump(query, credentials=None):
    """Pretty-print the inbound handshake Django received.

    ``query`` is the request's GET data. ``credentials`` is the
    toolkit's parsed authorization request when this view already has
    it. The oauthlib request object and any secret values are left out.
    """
    payload = {
        'query_string': query.urlencode(),
        'query': _public_items(query.lists()),
    }
    if credentials is not None:
        payload['credentials'] = _public_credentials(credentials)
    return json.dumps(payload, indent=2, sort_keys=True)


def _public_items(pairs):
    dumped = {}
    for key, values in pairs:
        if key in _HANDSHAKE_SECRET_KEYS:
            dumped[key] = '[omitted]'
        else:
            dumped[key] = list(values)
    return dumped


def _public_credentials(credentials):
    dumped = {}
    for key in sorted(credentials):
        if key == 'request' or key in _HANDSHAKE_SECRET_KEYS:
            if key in _HANDSHAKE_SECRET_KEYS:
                dumped[key] = '[omitted]'
            continue
        value = _json_ready(credentials[key])
        if value is not _SKIP:
            dumped[key] = value
    return dumped


class _SkipType:
    pass


_SKIP = _SkipType()


def _json_ready(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        ready = [_json_ready(item) for item in value]
        if any(item is _SKIP for item in ready):
            return _SKIP
        return ready
    if isinstance(value, dict):
        ready = {}
        for key, item in value.items():
            dumped = _json_ready(item)
            if dumped is _SKIP:
                return _SKIP
            ready[str(key)] = dumped
        return ready
    return _SKIP


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def authorization_url(redirect_uri, state=DEV_STATE, resource=None):
    from django.urls import reverse

    if resource is None:
        resource = MCP_RESOURCE
    query = urlencode({
        'response_type': 'code',
        'client_id': MCP_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'scope': ' '.join(REQUESTED_SCOPES),
        'state': state,
        'code_challenge': pkce_challenge(DEV_CODE_VERIFIER),
        'code_challenge_method': 'S256',
        'resource': resource,
    })
    return '%s?%s' % (reverse('oauth2_provider:authorize'), query)


def ensure_mcp_application():
    """Register the example public client. Safe to run again.

    Authorization-code only, PKCE at the authorize view, no client
    secret, and ``skip_authorization`` left off. Nothing here is a
    delegated grant.
    """
    application_model = get_application_model()
    defaults = {
        'name': MCP_CLIENT_NAME,
        'client_type': application_model.CLIENT_PUBLIC,
        'authorization_grant_type': application_model.GRANT_AUTHORIZATION_CODE,
        'redirect_uris': ' '.join(REDIRECT_URIS),
        'skip_authorization': False,
        'client_secret': '',
        'hash_client_secret': False,
        'algorithm': '',
    }
    application, created = application_model.objects.get_or_create(
        client_id=MCP_CLIENT_ID,
        defaults=defaults,
    )
    if not created:
        for field, value in defaults.items():
            setattr(application, field, value)
    application.full_clean()
    application.save()
    return application


class ExampleAccessTokenValidator(OAuth2Validator):
    """Keep the toolkit's access token and drop its refresh token.

    Authorization-code grants otherwise also store a refresh token.
    This example does not add a refresh-token or long-lived grant.
    """

    def _save_bearer_token(self, token, request, *args, **kwargs):
        token.pop('refresh_token', None)
        return super()._save_bearer_token(token, request, *args, **kwargs)


class McpAuthorizationView(AuthorizationView):
    """Validated OAuth request, then the blank picker.

    Anonymous requests still redirect to ``LOGIN_URL`` through the
    toolkit's ``LoginRequiredMixin``. Approve uses the toolkit to
    redirect with an authorization code. Cancel uses its deny path.
    """

    template_name = 'example/mcp_authorize.html'

    def get(self, request, *args, **kwargs):
        try:
            scopes, credentials = self.validate_authorization_request(request)
        except OAuthToolkitError as error:
            return self.error_response(error, application=None)

        application = get_application_model().objects.get(
            client_id=credentials['client_id'],
        )
        # validate_authorization_request still accepts "plain". The
        # toolkit gate rejects it later, when the code would be saved.
        # Refuse it here so the consent page is not shown for a method
        # this server will not store.
        if (
            credentials.get('code_challenge_method') == 'plain'
            and oauth2_settings.COMPLIANT_BCP_RFC9700_PKCE_METHOD
        ):
            error = OAuthToolkitError(
                error=CustomOAuth2Error(
                    error='invalid_request',
                    description=(
                        'Unsupported "plain" code_challenge_method; use "S256".'
                    ),
                    state=credentials.get('state'),
                ),
                redirect_uri=credentials.get('redirect_uri'),
            )
            return self.error_response(error, application)
        resources = request.GET.getlist('resource')
        for uri in resources:
            if not is_valid_resource_uri(uri):
                error = OAuthToolkitError(
                    error=CustomOAuth2Error(
                        error='invalid_target',
                        description=(
                            "The resource '%s' is not a valid resource indicator."
                            % uri
                        ),
                        state=credentials.get('state'),
                    ),
                    redirect_uri=credentials.get('redirect_uri'),
                )
                return self.error_response(error, application)
        if resources:
            credentials = dict(credentials)
            credentials['resource'] = ' '.join(resources)
        context = self._picker_context(scopes, credentials, application)
        self.oauth2_data = context
        context['handshake_dump'] = handshake_dump(request.GET, credentials)
        context['form'] = self.get_form(self.get_form_class())
        return self.render_to_response(self.get_context_data(**context))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault('handshake_dump', handshake_dump(self.request.GET))
        return context

    def error_response(self, error, application, **kwargs):
        from oauth2_provider.views.mixins import OAuthLibMixin

        redirect, error_response = OAuthLibMixin.error_response(self, error, **kwargs)
        if redirect:
            return self.redirect(error_response['url'], application)
        error_response['handshake_dump'] = handshake_dump(self.request.GET)
        status = error_response['error'].status_code
        return self.render_to_response(error_response, status=status)

    def form_invalid(self, form):
        context = self._context_from_form(form)
        seen = {
            'client_id': context.get('client_id'),
            'redirect_uri': context.get('redirect_uri'),
            'response_type': context.get('response_type'),
            'state': context.get('state'),
            'scope': ' '.join(context.get('scopes') or []),
            'code_challenge': context.get('code_challenge'),
            'code_challenge_method': context.get('code_challenge_method'),
            'resource': form.data.get('resource'),
        }
        context['handshake_dump'] = handshake_dump(self.request.GET, {
            key: value for key, value in seen.items() if value
        })
        return self.render_to_response(self.get_context_data(**context))

    def _context_from_form(self, form):
        client_id = form.cleaned_data.get('client_id') or form.data.get('client_id')
        application = None
        if client_id:
            application = get_application_model().objects.filter(
                client_id=client_id,
            ).first()
        scope_text = form.cleaned_data.get('scope')
        if scope_text is None:
            scope_text = form.data.get('scope') or ''
        scopes = [scope for scope in scope_text.split() if scope]
        return self._picker_context(scopes, {
            'client_id': client_id,
            'redirect_uri': form.cleaned_data.get('redirect_uri'),
            'response_type': form.cleaned_data.get('response_type'),
            'state': form.cleaned_data.get('state'),
            'code_challenge': form.cleaned_data.get('code_challenge'),
            'code_challenge_method': form.cleaned_data.get('code_challenge_method'),
        }, application)

    def _picker_context(self, scopes, credentials, application):
        descriptions = get_scopes_backend().get_all_scopes()
        context = {
            'scopes': list(scopes),
            'scopes_descriptions': [
                descriptions.get(scope, scope) for scope in scopes
            ],
            'application': application,
            'client_id': credentials.get('client_id'),
            'redirect_uri': credentials.get('redirect_uri'),
            'response_type': credentials.get('response_type'),
            'state': credentials.get('state'),
        }
        if credentials.get('code_challenge'):
            context['code_challenge'] = credentials['code_challenge']
        if credentials.get('code_challenge_method'):
            context['code_challenge_method'] = credentials['code_challenge_method']
        if credentials.get('nonce'):
            context['nonce'] = credentials['nonce']
        if credentials.get('claims'):
            context['claims'] = credentials['claims']
        if credentials.get('resource'):
            context['resource'] = credentials['resource']
        return context


def mcp_callback(request):
    """Show the redirect result. Does not exchange a code."""
    return render(request, 'example/mcp_callback.html', {
        'code': request.GET.get('code', ''),
        'error': request.GET.get('error', ''),
        'handshake_dump': handshake_dump(request.GET),
    })


def _metadata_url(request):
    return request.build_absolute_uri(
        '/.well-known/oauth-protected-resource/mcp'
    )


def _token_required(request):
    """Return a 401 challenge, or None when the bearer token is known.

    ``scopes=[]`` does not skip the RFC 8707 audience check. When the
    access token stores a resource list, ``verify_request`` rejects it
    unless this request's absolute URI is under that list. An empty
    list stays unrestricted.
    """
    core = OAuthLibCore(Server(OAuth2Validator()))
    valid, oauthlib_request = core.verify_request(request, scopes=[])
    if valid:
        request.resource_owner = oauthlib_request.user
        return None
    challenge = build_bearer_challenge(
        request,
        oauth2_error=getattr(oauthlib_request, 'oauth2_error', None),
        resource_metadata_url=_metadata_url(request),
    )
    response = HttpResponse(
        status=challenge_status(getattr(oauthlib_request, 'oauth2_error', None)),
    )
    response['WWW-Authenticate'] = challenge
    return response


def _rpc_result(message, result):
    return JsonResponse({
        'jsonrpc': '2.0',
        'id': message.get('id'),
        'result': result,
    })


def _rpc_error(message, code, text, status=200):
    return JsonResponse(
        {
            'jsonrpc': '2.0',
            'id': None if message is None else message.get('id'),
            'error': {'code': code, 'message': text},
        },
        status=status,
    )


@csrf_exempt
def mcp_http(request):
    """Example MCP endpoint. The bearer token is a test credential only.

    ``initialize`` and ``tools/list`` are the whole protocol surface.
    A missing or unknown token is rejected. Repository selection is
    not read and is not enforced here.
    """
    denied = _token_required(request)
    if denied is not None:
        return denied
    if request.method != 'POST':
        return HttpResponse(status=405)
    try:
        message = json.loads(request.body.decode('utf-8') or 'null')
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _rpc_error(None, -32700, 'Parse error')
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, 'Invalid request')
    method = message.get('method')
    if message.get('id') is None and str(method).startswith('notifications/'):
        return HttpResponse(status=202)
    if method == 'initialize':
        params = message.get('params') or {}
        version = params.get('protocolVersion') or MCP_PROTOCOL_VERSION
        return _rpc_result(message, {
            'protocolVersion': version,
            'capabilities': {'tools': {}},
            'serverInfo': {
                'name': MCP_SERVER_NAME,
                'version': '0.0.0',
            },
            'instructions': (
                'Test credential for this example endpoint only.'
            ),
        })
    if method == 'tools/list':
        return _rpc_result(message, {'tools': []})
    if method == 'ping':
        return _rpc_result(message, {})
    return _rpc_error(message, -32601, 'Method not found')
