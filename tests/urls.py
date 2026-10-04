from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Permission
from django.urls import path

from example.models import User

# The example user has no admin of its own. Stock UserAdmin does not
# call gh_permissions.services, so creating or renaming a user here
# does not reserve an alias or a personal organization.
# auth.Permission is the operation catalog. A bare ModelAdmin keeps it
# outside the organization scope mixin.
admin.site.register(User, UserAdmin)
admin.site.register(Permission)

urlpatterns = [
    path('admin/', admin.site.urls),
]
