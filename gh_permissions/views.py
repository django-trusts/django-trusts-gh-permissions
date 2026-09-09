from django.views.generic import DetailView, ListView
from trusts.decorators import require_authorized
from trusts.views import AuthorizedObjectMixin, AuthorizedQuerySetMixin

from gh_permissions.models import Repository


class RepositoryListView(AuthorizedQuerySetMixin, ListView):
    model = Repository
    list_operation = 'read'
    paginate_by = 20
    ordering = ('pk',)


class RepositoryDetailView(AuthorizedObjectMixin, DetailView):
    model = Repository
    object_operation = 'read'
    pk_url_kwarg = 'pk'


@require_authorized('read', resource_model=Repository, resource_kwarg='pk')
def repository_read(request, pk):
    """Function view gated by the framework decorator. Body is not policy."""
    from django.http import HttpResponse

    return HttpResponse('ok')
