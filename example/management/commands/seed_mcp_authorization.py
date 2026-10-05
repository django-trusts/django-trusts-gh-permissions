"""Register the example MCP client and print how to reach it.

Does not start a server. ``runserver`` on port 8000 serves the URLs
this command prints. The access token is only a test credential for
the example MCP endpoint.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from example.mcp_authorization import (
    LOCAL_MCP_URL,
    LOCAL_REDIRECT_URI,
    MCP_CLIENT_ID,
    OAUTH_TOOLKIT_VERSION,
    REQUESTED_SCOPES,
    authorization_url,
    ensure_mcp_application,
    installed_oauth_toolkit_version,
)


class Command(BaseCommand):
    help = (
        'Register the example MCP authorization client and print the '
        'local MCP URL. The access token is only a test credential.'
    )

    def handle(self, *args, **options):
        installed = installed_oauth_toolkit_version()
        if installed != OAUTH_TOOLKIT_VERSION:
            raise CommandError(
                'django-oauth-toolkit %s is installed; this spike expects %s.'
                % (installed, OAUTH_TOOLKIT_VERSION)
            )
        ensure_mcp_application()
        snippet = {
            'mcpServers': {
                'django-trusts-example': {
                    'url': LOCAL_MCP_URL,
                    'auth': {
                        'CLIENT_ID': MCP_CLIENT_ID,
                        'scopes': list(REQUESTED_SCOPES),
                    },
                },
            },
        }
        self.stdout.write('django-oauth-toolkit %s' % installed)
        self.stdout.write('MCP authorization client: %s' % MCP_CLIENT_ID)
        self.stdout.write('MCP URL: %s' % LOCAL_MCP_URL)
        self.stdout.write(authorization_url(LOCAL_REDIRECT_URI))
        self.stdout.write(
            'The access token is only a test credential for %s. '
            'It does not grant repository access.'
            % LOCAL_MCP_URL
        )
        self.stdout.write(
            'Open the authorize URL after python manage.py runserver. '
            'Sign in when prompted. Approve returns an authorization code. '
            'Cancel returns access_denied and issues nothing.'
        )
        self.stdout.write(
            'Point Cursor at this server with:\n%s'
            % json.dumps(snippet, indent=2)
        )
