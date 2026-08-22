import logging

from celery import shared_task

from apps.library.services import scan_music_library

logger = logging.getLogger(__name__)


@shared_task
def scan_library_task(full: bool = False) -> dict:
    """Background library filesystem scan (CLAUDE.md #6/#25). Triggered
    automatically once per `web` container start (quick sync only — see
    docker/entrypoint.sh) and on demand from the navbar "Library sync"
    menu (staff only — apps.library.views.library_scan_quick/_full) or
    `manage.py scan_library`.
    """
    report = scan_music_library(full=full)
    logger.info("Library scan finished (full=%s): %s", full, report)
    return {
        "files_found": report.files_found,
        "tracks_added": report.tracks_added,
        "tracks_removed": report.tracks_removed,
        "errors": report.errors,
    }
