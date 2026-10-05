from django.contrib import admin
from django.contrib.auth.models import Permission
from django.contrib.auth.views import LoginView
from django.urls import path

from example.mcp_authorization import McpAuthorizationView, mcp_callback
from example.models import User
from gh_permissions.admin import ServiceBackedUserAdmin

# User create, rename, and delete go through gh_permissions.services so
# the alias and personal organization stay in step with the username.
# auth.Permission is the operation catalog. A bare ModelAdmin keeps it
# outside the organization scope mixin.
admin.site.register(User, ServiceBackedUserAdmin)
admin.site.register(Permission)


def _hide_oauth_toolkit_admin():
    """Keep the toolkit's token and application admin out of this example.

    The package registers those models on import. This spike does not
    issue grants from the admin either.
    """
    for model in list(admin.site._registry):
        if model._meta.app_label == 'oauth2_provider':
            admin.site.unregister(model)


_hide_oauth_toolkit_admin()

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/login/', LoginView.as_view(), name='login'),
    path('o/authorize/', McpAuthorizationView.as_view(), name='mcp-authorize'),
    path('mcp/callback/', mcp_callback, name='mcp-callback'),
]
