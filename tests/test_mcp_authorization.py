"""MCP authorize entry stops at a blank connected-app picker.

Proves the login redirect, the return to that page, that the page
requires a session, and that approve and cancel issue no grant.
"""

from html.parser import HTMLParser
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from example.mcp_authorization import (
    DEV_STATE,
    LOCAL_REDIRECT_URI,
    MCP_CLIENT_ID,
    OAUTH_TOOLKIT_VERSION,
    REQUESTED_SCOPES,
    TEST_REDIRECT_URI,
    authorization_url,
    ensure_mcp_application,
    installed_oauth_toolkit_version,
)
from oauth2_provider.models import AccessToken, Grant, IDToken, RefreshToken


PASSWORD = 'mcp-human-dev-only'
ROOT = Path(__file__).resolve().parents[1]
# force_login must name ModelBackend. GhAuthorizationBackend is first in
# the runnable settings and does not load a session user. This is not an
# AUTHENTICATION_BACKENDS override. The return-to test signs in through
# the login form instead.
MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'


class _InputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag != 'input':
            return
        name = attrs.get('name')
        if name and name not in self.inputs:
            self.inputs[name] = attrs.get('value', '')


def _posted_fields(html):
    parser = _InputParser()
    parser.feed(html)
    return parser.inputs


def _authority_counts():
    return {
        'grants': Grant.objects.count(),
        'access_tokens': AccessToken.objects.count(),
        'refresh_tokens': RefreshToken.objects.count(),
        'id_tokens': IDToken.objects.count(),
    }


class McpAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='mcp-human',
            password=PASSWORD,
        )

    def setUp(self):
        self.authorize = authorization_url(TEST_REDIRECT_URI)
        ensure_mcp_application()
        self.client = Client()

    def test_package_pin_matches_the_installed_distribution(self):
        self.assertEqual(installed_oauth_toolkit_version(), OAUTH_TOOLKIT_VERSION)
        self.assertTrue(
            set(REQUESTED_SCOPES) <= set(settings.OAUTH2_PROVIDER['SCOPES'])
        )

    def test_library_and_admin_do_not_own_the_package(self):
        offenders = []
        for path in (ROOT / 'gh_permissions').rglob('*.py'):
            text = path.read_text()
            if 'oauth2_provider' in text or 'django_oauth_toolkit' in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])
        reverse('mcp-authorize')
        labels = {model._meta.app_label for model in admin.site._registry}
        self.assertNotIn('oauth2_provider', labels)

    def test_anonymous_authorization_redirects_to_django_login(self):
        response = self.client.get(self.authorize)
        self.assertEqual(response.status_code, 302)
        location = urlparse(response.url)
        self.assertEqual(location.path, reverse('login'))
        next_url = parse_qs(location.query)['next'][0]
        self.assertTrue(next_url.startswith('/o/authorize/'))
        self.assertIn('client_id=%s' % MCP_CLIENT_ID, next_url)
        login = self.client.get(response.url)
        self.assertEqual(login.status_code, 200)
        self.assertContains(login, 'name="username"')
        self.assertContains(login, 'name="password"')
        self.assertNotContains(login, 'All repositories')
        self.assertNotContains(login, 'Requested permissions')
        posted = self.client.post(self.authorize, {'allow': 'true'})
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(urlparse(posted.url).path, reverse('login'))
        self.assertEqual(_authority_counts(), {
            'grants': 0,
            'access_tokens': 0,
            'refresh_tokens': 0,
            'id_tokens': 0,
        })

    def test_login_returns_to_the_blank_picker(self):
        anonymous = self.client.get(self.authorize)
        next_url = parse_qs(urlparse(anonymous.url).query)['next'][0]
        logged_in = self.client.post(reverse('login'), {
            'username': self.user.username,
            'password': PASSWORD,
            'next': next_url,
        })
        self.assertEqual(logged_in.status_code, 302)
        self.assertEqual(logged_in.url, next_url)
        picker = self.client.get(logged_in.url)
        self.assertEqual(picker.status_code, 200)
        self._assert_blank_picker(picker)

    def test_picker_requires_an_authenticated_session(self):
        anonymous = Client()
        denied = anonymous.get(self.authorize)
        self.assertEqual(denied.status_code, 302)
        self.assertNotIn('All repositories', denied.content.decode())
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        picker = self.client.get(self.authorize)
        self.assertEqual(picker.status_code, 200)
        self._assert_blank_picker(picker)

    def test_cancel_returns_access_denied_without_a_grant(self):
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        page = self.client.get(self.authorize)
        fields = _posted_fields(page.content.decode())
        self.assertNotIn('allow', fields)
        cancelled = self.client.post(self.authorize, fields)
        self.assertEqual(cancelled.status_code, 302)
        parsed = urlparse(cancelled.url)
        query = parse_qs(parsed.query)
        self.assertEqual(query['error'], ['access_denied'])
        self.assertEqual(query['state'], [DEV_STATE])
        self.assertNotIn('code', query)
        self.assertEqual(_authority_counts(), {
            'grants': 0,
            'access_tokens': 0,
            'refresh_tokens': 0,
            'id_tokens': 0,
        })
        landing = self.client.get(cancelled.url)
        self.assertEqual(landing.status_code, 200)
        self.assertContains(landing, 'Authorization cancelled')
        self.assertContains(landing, 'No access was granted.')
        ignored = self.client.get(reverse('mcp-callback'), {'code': 'not-a-grant'})
        self.assertContains(ignored, 'Authorization code ignored')
        self.assertContains(
            ignored,
            'does not exchange an authorization code',
        )
        self.assertEqual(_authority_counts(), {
            'grants': 0,
            'access_tokens': 0,
            'refresh_tokens': 0,
            'id_tokens': 0,
        })

    def test_approve_does_not_issue_delegated_authority(self):
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        page = self.client.get(self.authorize)
        fields = _posted_fields(page.content.decode())
        fields['allow'] = 'true'
        approved = self.client.post(self.authorize, fields)
        self.assertEqual(approved.status_code, 200)
        self.assertContains(approved, 'No delegated authority was issued.')
        self._assert_blank_picker(approved)
        self.assertNotIn('code=', approved.content.decode())
        self.assertEqual(_authority_counts(), {
            'grants': 0,
            'access_tokens': 0,
            'refresh_tokens': 0,
            'id_tokens': 0,
        })

    def test_skip_authorization_still_shows_the_picker(self):
        application = ensure_mcp_application()
        application.skip_authorization = True
        application.save(update_fields=['skip_authorization'])
        self.client.force_login(self.user, backend=MODEL_BACKEND)
        picker = self.client.get(self.authorize + '&approval_prompt=auto')
        self.assertEqual(picker.status_code, 200)
        self._assert_blank_picker(picker)
        self.assertEqual(_authority_counts(), {
            'grants': 0,
            'access_tokens': 0,
            'refresh_tokens': 0,
            'id_tokens': 0,
        })

    def test_seed_command_prints_the_local_authorize_url(self):
        output = StringIO()
        call_command('seed_mcp_authorization', stdout=output)
        text = output.getvalue()
        self.assertIn('django-oauth-toolkit %s' % OAUTH_TOOLKIT_VERSION, text)
        self.assertIn(MCP_CLIENT_ID, text)
        self.assertIn('/o/authorize/', text)
        self.assertIn('redirect_uri=%s' % LOCAL_REDIRECT_URI.replace(':', '%3A').replace('/', '%2F'), text)
        call_command('seed_mcp_authorization', stdout=StringIO())
        from oauth2_provider.models import get_application_model
        self.assertEqual(
            get_application_model().objects.filter(client_id=MCP_CLIENT_ID).count(),
            1,
        )

    def _assert_blank_picker(self, response):
        self.assertContains(response, 'id="account-selection"')
        self.assertContains(response, 'Account or organization')
        self.assertContains(response, 'Select an account or organization')
        self.assertContains(response, 'id="repository-access"')
        self.assertContains(response, 'All repositories')
        self.assertContains(response, 'Only select repositories')
        self.assertContains(response, 'id="requested-permissions"')
        self.assertContains(response, 'Requested permissions')
        self.assertContains(response, 'Read access to repository contents')
        self.assertContains(
            response, 'Read and write access to repository contents',
        )
        self.assertContains(response, 'Signed in as')
        self.assertContains(response, self.user.username)
        self.assertContains(response, '>Approve<')
        self.assertContains(response, '>Cancel<')
        self.assertContains(response, 'disabled')
        self.assertContains(response, 'Account selection is not saved.')
        self.assertContains(response, 'Repository selection is not saved.')
