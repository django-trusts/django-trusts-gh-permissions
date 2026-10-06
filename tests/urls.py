from django.contrib import admin
from django.contrib.auth.models import Permission
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from example.api import RepositoryViewSet
from example.models import User
from gh_permissions.admin import ServiceBackedUserAdmin

# User create, rename, and delete go through gh_permissions.services so
# the alias and personal organization stay in step with the username.
# auth.Permission is the operation catalog. A bare ModelAdmin keeps it
# outside the organization scope mixin.
admin.site.register(User, ServiceBackedUserAdmin)
admin.site.register(Permission)

router = DefaultRouter()
router.register('repositories', RepositoryViewSet, basename='repository')

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include(router.urls)),
]
