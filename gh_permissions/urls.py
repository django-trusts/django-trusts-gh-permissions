from django.urls import path

from gh_permissions.views import (
    RepositoryDetailView,
    RepositoryListView,
    repository_read,
)

urlpatterns = [
    path('repositories/', RepositoryListView.as_view(), name='gh-repository-list'),
    path(
        'repositories/<int:pk>/',
        RepositoryDetailView.as_view(),
        name='gh-repository-detail',
    ),
    path(
        'repositories/<int:pk>/read/',
        repository_read,
        name='gh-repository-read',
    ),
]
