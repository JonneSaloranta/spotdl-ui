from django.urls import path

from apps.core import health_views

urlpatterns = [
    path("health/", health_views.liveness, name="health"),
    path("ready/", health_views.readiness, name="ready"),
]
