"""Register the example MCP client and print the authorize URL.

Does not start a server and does not issue a grant. ``runserver`` on
port 8000 serves the URL this command prints.
"""

from django.core.management.base import BaseCommand, CommandError

from example.mcp_authorization import (
    LOCAL_REDIRECT_URI,
    MCP_CLIENT_ID,
    OAUTH_TOOLKIT_VERSION,
    authorization_url,
    ensure_mcp_application,
    installed_oauth_toolkit_version,
)


class Command(BaseCommand):
    help = (
        'Register the example MCP authorization client and print the '
        'local authorize URL. Approve does not issue a grant.'
    )

    def handle(self, *args, **options):
        installed = installed_oauth_toolkit_version()
        if installed != OAUTH_TOOLKIT_VERSION:
            raise CommandError(
                'django-oauth-toolkit %s is installed; this spike expects %s.'
                % (installed, OAUTH_TOOLKIT_VERSION)
            )
        ensure_mcp_application()
        self.stdout.write('django-oauth-toolkit %s' % installed)
        self.stdout.write('MCP authorization client: %s' % MCP_CLIENT_ID)
        self.stdout.write(authorization_url(LOCAL_REDIRECT_URI))
        self.stdout.write(
            'Open that URL after python manage.py runserver. '
            'Sign in when prompted. Approve does not issue a grant. '
            'Cancel returns access_denied and does not issue a grant.'
        )
