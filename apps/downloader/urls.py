from django.urls import path

from apps.downloader import views

app_name = "downloader"

urlpatterns = [
    path("batches/submit/", views.submit_batch, name="submit"),
    path("batches/<int:pk>/", views.batch_detail, name="batch_detail"),
    path("batches/<int:pk>/status/", views.batch_status_partial, name="batch_status"),
    path("batches/<int:pk>/cancel/", views.batch_cancel, name="batch_cancel"),
    path("batches/<int:pk>/retry-failed/", views.batch_retry_failed, name="batch_retry_failed"),
    path("batches/<int:pk>/retry-all/", views.batch_retry_all, name="batch_retry_all"),
    path("items/<int:pk>/retry/", views.item_retry, name="item_retry"),
    path("items/<int:pk>/cancel/", views.item_cancel, name="item_cancel"),
]
