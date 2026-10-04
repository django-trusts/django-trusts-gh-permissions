from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import Permission
from django.urls import path

from example.models import User

# The example user has no admin of its own. Stock UserAdmin lets the
# organization-owner proof show that account pages stay closed.
# auth.Permission is the operation catalog. A bare ModelAdmin keeps it
# outside the organization scope mixin.
admin.site.register(User, UserAdmin)
admin.site.register(Permission)

urlpatterns = [
    path('admin/', admin.site.urls),
]
