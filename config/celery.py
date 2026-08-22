"""Celery application for the spotDL Web UI project.

Workers are stateless and restart-safe: task state lives in PostgreSQL
(DownloadBatch/DownloadItem) rather than in worker memory, so a restarted
worker can resume by re-reading the database (see apps.downloader.tasks).
"""

import os

from celery import Celery
from celery.signals import setup_logging as celery_setup_logging_signal

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.base")

app = Celery("spotdl_ui")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@celery_setup_logging_signal.connect
def _use_django_logging(**kwargs):
    """Let Celery reuse Django's LOGGING configuration instead of its own."""
    from logging.config import dictConfig

    from django.conf import settings

    dictConfig(settings.LOGGING)
