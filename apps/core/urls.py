from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("offline/", views.offline, name="offline"),
    path("settings/", views.site_settings_view, name="settings"),
]
