from django.urls import path

from apps.sharing import views

app_name = "sharing"

urlpatterns = [
    path("<str:token>/status/", views.shared_status_partial, name="status"),
    path("<str:token>/track/<int:track_id>/stream/", views.shared_track_stream, name="track_stream"),
    path("<str:token>/track/<int:track_id>/cover/", views.shared_track_cover, name="track_cover"),
    path("<str:token>/", views.shared_submit, name="submit"),
]
