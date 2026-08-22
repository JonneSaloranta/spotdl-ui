from django.urls import path

from apps.musicbrainz import views

app_name = "musicbrainz"

urlpatterns = [
    path("tracks/<int:pk>/musicbrainz/", views.review, name="review"),
    path("tracks/<int:pk>/musicbrainz/<str:mbid>/preview/", views.preview, name="preview"),
    path("tracks/<int:pk>/musicbrainz/<str:mbid>/apply/", views.apply, name="apply"),
]
