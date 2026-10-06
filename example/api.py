"""Bounded DRF example for repository reads and one service-backed create.

DRF is not a ``gh_permissions`` dependency. This module is the runnable
example. List and retrieve share one permission string and one
queryset. Create calls ``gh_permissions.services.create_repository``
rather than inserting a row here.

``user.has_perm`` is only called with an ``app_label.codename`` string.
``.authorized()`` takes the ``auth.Permission`` row for that codename
on the protected model's content type. A codename is unique per
content type, not per app, so the read queryset binds ``Repository``.
"""

from django.contrib.auth.models import Permission
from django.http import Http404
from rest_framework import mixins, serializers, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission

from example.authentication import BearerTokenAuthentication
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


def _permission_row(code, model):
    """``auth.Permission`` for ``code`` on ``model``'s content type.

    The action map stays a permission string. The model is the queryset
    being authorized, not a second map. For list and retrieve that
    model is ``Repository``.
    """
    app_label, codename = code.split('.', 1)
    return Permission.objects.get(
        content_type__app_label=app_label,
        content_type__model=model._meta.model_name,
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

    Retrieve uses DRF's ``get_object`` on the authorized queryset, so a
    missing or malformed primary key is 404. An object-permission
    failure is also 404, not 403. The body is DRF's not-found detail
    and does not include the repository. List is an empty page for an
    authenticated caller with no rows. Bearer token authentication is
    tried before session authentication. Anonymous requests are rejected
    by authentication before either path.

    Create has no repository object. ``create_repository`` locks the
    submitted organization and requires ``manage_organization``,
    including the active-superuser rule that ``.authorized()`` does not
    copy. A denied or missing parent is 403 and writes nothing.
    """

    serializer_class = RepositorySerializer
    authentication_classes = (
        BearerTokenAuthentication,
        SessionAuthentication,
    )
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
            user, _permission_row(code, Repository),
        ).order_by('pk')

    def get_object(self):
        """DRF's lookup, then one not-found detail.

        ``GenericAPIView.get_object`` already 404s when the primary key
        is missing or the wrong type. Django's shortcut puts the model
        name in that message. Re-raising a bare ``Http404`` keeps
        malformed, missing, and unauthorized keys on the same body.
        """
        try:
            return super().get_object()
        except Http404:
            raise Http404

    def check_object_permissions(self, request, obj):
        """404 when the object check disagrees with the queryset.

        DRF's ``get_object`` already turns a missing row and a malformed
        primary key into 404. Its object-permission failure is 403,
        which would confirm that the primary key exists.
        """
        try:
            super().check_object_permissions(request, obj)
        except PermissionDenied:
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
