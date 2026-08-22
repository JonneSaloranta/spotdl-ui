from django.urls import path

from apps.library import views

app_name = "library"

urlpatterns = [
    path("", views.track_list, name="list"),
    path("duplicates/", views.remove_duplicates, name="remove_duplicates"),
    path("scan/quick/", views.library_scan_quick, name="scan_quick"),
    path("scan/full/", views.library_scan_full, name="scan_full"),
    path("<int:pk>/stream/", views.track_stream, name="stream"),
    path("<int:pk>/cover/", views.track_cover, name="cover"),
    path("<int:pk>/neighbors/", views.track_neighbors, name="neighbors"),
]
