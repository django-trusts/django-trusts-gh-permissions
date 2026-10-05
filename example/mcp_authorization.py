"""MCP authorization entry for the runnable example.

``django-oauth-toolkit`` validates the authorization request and the
login mixin sends an anonymous human to Django's login. This module
owns the consent step so the package can be replaced: the screen is a
blank connected-app picker, cancel returns ``access_denied``, and
approve does not create a grant or a token.

The toolkit's own ``AuthorizationView.get`` can skip that screen and
mint a code when ``skip_authorization`` or ``approval_prompt=auto``
says so. This view does not call that path.
"""

import base64
import hashlib
import importlib.metadata
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from oauth2_provider.exceptions import OAuthToolkitError
from oauth2_provider.models import get_application_model
from oauth2_provider.scopes import get_scopes_backend
from oauth2_provider.views import AuthorizationView


OAUTH_TOOLKIT_DISTRIBUTION = 'django-oauth-toolkit'
OAUTH_TOOLKIT_VERSION = '3.4.1'

MCP_CLIENT_ID = 'example-mcp-client'
MCP_CLIENT_NAME = 'Example MCP client'
LOCAL_REDIRECT_URI = 'http://localhost:8000/mcp/callback/'
TEST_REDIRECT_URI = 'http://testserver/mcp/callback/'
REDIRECT_URIS = (LOCAL_REDIRECT_URI, TEST_REDIRECT_URI)
MCP_RESOURCE = 'http://localhost:8000/mcp/'
REQUESTED_SCOPES = ('read_repository', 'write_repository')
# Development-only PKCE verifier so the printed authorize URL is stable.
# Not a credential and not a grant.
DEV_CODE_VERIFIER = 'mcp-spike-dev-verifier-not-a-secret-0123456789'
DEV_STATE = 'mcp-spike'


def installed_oauth_toolkit_version():
    return importlib.metadata.version(OAUTH_TOOLKIT_DISTRIBUTION)


def pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')


def authorization_url(redirect_uri, state=DEV_STATE):
    from django.urls import reverse

    query = urlencode({
        'response_type': 'code',
        'client_id': MCP_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'scope': ' '.join(REQUESTED_SCOPES),
        'state': state,
        'code_challenge': pkce_challenge(DEV_CODE_VERIFIER),
        'code_challenge_method': 'S256',
        'resource': MCP_RESOURCE,
    })
    return '%s?%s' % (reverse('mcp-authorize'), query)


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


class McpAuthorizationView(AuthorizationView):
    """Validated OAuth request, then the blank picker.

    Anonymous requests still redirect to ``LOGIN_URL`` through the
    toolkit's ``LoginRequiredMixin``. Approving re-renders this page
    and does not call ``create_authorization_response``.
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
        context = self._picker_context(scopes, credentials, application)
        self.oauth2_data = context
        context['form'] = self.get_form(self.get_form_class())
        return self.render_to_response(self.get_context_data(**context))

    def form_valid(self, form):
        if form.cleaned_data.get('allow'):
            return self._approval_refused(form)
        return super().form_valid(form)

    def form_invalid(self, form):
        return self.render_to_response(self.get_context_data(
            **self._context_from_form(form),
        ))

    def create_authorization_response(self, request, scopes, credentials, allow):
        # Cancel (allow=False) is the toolkit's access_denied redirect.
        # allow=True would store a grant and return a code. Refuse that
        # even if a later edit calls this method.
        if allow:
            raise PermissionDenied(
                'This spike does not issue an authorization grant.',
            )
        return super().create_authorization_response(
            request, scopes, credentials, allow,
        )

    def _approval_refused(self, form):
        context = self._context_from_form(form)
        context['approval_not_issued'] = True
        context['form'] = form
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
        return context


def mcp_callback(request):
    """Landing page for cancel. Does not exchange a code or store a grant."""
    return render(request, 'example/mcp_callback.html', {
        'code': request.GET.get('code', ''),
        'error': request.GET.get('error', ''),
    })
