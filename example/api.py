"""Bounded DRF example for repository reads and one service-backed create.

DRF is not a ``gh_permissions`` dependency. This module is the runnable
example. List and retrieve share one permission string and one
queryset. Create calls ``gh_permissions.services.create_repository``
rather than inserting a row here.

``user.has_perm`` is only called with a ``app_label.codename`` string.
``.authorized()`` takes the ``auth.Permission`` row that string names.
The lookup is Django's public permission string (application label and
codename). There is no extra content-type filter.
"""

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import DataError
from django.http import Http404
from rest_framework import mixins, serializers, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission

from gh_permissions.models import Repository
from gh_permissions.services import (
    DuplicateRelationshipTarget,
    ManagementDenied,
    MissingRelationshipTarget,
    UndefinedRelationshipWrite,
    create_repository,
)
from trusts.query import is_active_principal


READ_REPOSITORY = 'gh_permissions.read_repository'
MANAGE_ORGANIZATION = 'gh_permissions.manage_organization'

# The only action → permission map. List and retrieve are the same read.
ACTION_PERMISSIONS = {
    'list': READ_REPOSITORY,
    'retrieve': READ_REPOSITORY,
    'create': MANAGE_ORGANIZATION,
}

_NOT_ALLOWED = 'Not allowed.'


class RepositoryPagination(PageNumberPagination):
    """Page the queryset the view already restricted.

    ``page_size`` can be lowered in a request to show that an earlier
    unauthorized primary key is not the first row.
    """

    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class RepositorySerializer(serializers.Serializer):
    """Repository representation. Grants are not writable fields.

    Create accepts ``name`` and ``organization_id`` only. Any other
    key is rejected, including a repository, user, team, collaborator,
    or permission bundle.
    """

    id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(
        max_length=Repository._meta.get_field('name').max_length,
        trim_whitespace=False,
    )
    organization_id = serializers.IntegerField()

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError('Invalid payload.')
        unknown = set(data.keys()) - {'name', 'organization_id'}
        if unknown:
            raise serializers.ValidationError({
                field: 'Unknown field.' for field in sorted(unknown)
            })
        return super(RepositorySerializer, self).to_internal_value(data)


def _permission_row(code):
    """``auth.Permission`` for a Django ``app_label.codename`` string."""
    app_label, codename = code.split('.', 1)
    return Permission.objects.get(
        content_type__app_label=app_label,
        codename=codename,
    )


class RepositoryActionPermission(BasePermission):
    """Read the view's action map. Do not invent a second one.

    Authentication is required. An object-less ``has_perm`` is not used:
    ``ModelBackend`` can answer that, and it is not repository authority.
    Object checks use the mapped string and the object together.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        return view.action in view.action_permissions

    def has_object_permission(self, request, view, obj):
        code = view.action_permissions[view.action]
        return request.user.has_perm(code, obj)


class RepositoryViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """List and retrieve with ``read_repository``. Create via the service.

    Retrieve is 404 when the row is missing, the primary key is
    malformed, the caller cannot read it, or the object check disagrees
    with the queryset. The body is DRF's not-found detail and does not
    include the repository. List is an empty page for an authenticated
    caller with no rows. Anonymous requests are rejected by
    authentication before either path.

    Create has no repository object. ``create_repository`` locks the
    submitted organization and requires ``manage_organization``,
    including the active-superuser rule that ``.authorized()`` does not
    copy. A denied or missing parent is 403 and writes nothing.
    """

    serializer_class = RepositorySerializer
    authentication_classes = (SessionAuthentication,)
    permission_classes = (RepositoryActionPermission,)
    pagination_class = RepositoryPagination
    action_permissions = ACTION_PERMISSIONS

    def get_queryset(self):
        code = self.action_permissions.get(self.action)
        read_code = self.action_permissions['list']
        if code != read_code or code != self.action_permissions['retrieve']:
            return Repository.objects.none()
        user = self.request.user
        # ``.authorized()`` does not apply Django's inactive-principal
        # rule. ``is_active_principal`` is the public wrapper that keeps
        # this queryset aligned with ``user.has_perm`` for that case.
        # Active superusers stay on ``.authorized()``: ``has_perm`` is
        # true for them even when this queryset is empty.
        if not is_active_principal(user):
            return Repository.objects.none()
        return Repository.objects.authorized(
            user, _permission_row(code),
        ).order_by('pk')

    def get_object(self):
        queryset = self.filter_queryset(self.get_queryset())
        lookup = self.lookup_url_kwarg or self.lookup_field
        try:
            obj = queryset.get(**{self.lookup_field: self.kwargs[lookup]})
        except (
            Repository.DoesNotExist,
            ValueError,
            TypeError,
            ValidationError,
            OverflowError,
            DataError,
        ):
            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj

    def check_object_permissions(self, request, obj):
        for permission in self.get_permissions():
            if not permission.has_object_permission(request, self, obj):
                raise Http404

    def perform_create(self, serializer):
        if self.action_permissions.get(self.action) != MANAGE_ORGANIZATION:
            raise PermissionDenied(detail=_NOT_ALLOWED)
        try:
            serializer.instance = create_repository(
                self.request.user,
                serializer.validated_data['organization_id'],
                serializer.validated_data['name'],
            )
        except (ManagementDenied, MissingRelationshipTarget):
            raise PermissionDenied(detail=_NOT_ALLOWED)
        except UndefinedRelationshipWrite:
            raise serializers.ValidationError(_NOT_ALLOWED)
        except DuplicateRelationshipTarget:
            raise serializers.ValidationError({
                'name': 'That repository name is already used.',
            })
