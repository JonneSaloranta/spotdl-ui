"""Celery tasks for MusicBrainz metadata enrichment (CLAUDE.md #9).

Two separate entry points, both routing through the same
apps.musicbrainz.services.auto_enrich_track():

- enrich_track_task: queued once, right after a single track finishes
  downloading (apps.downloader.tasks.download_item_task) — cheap, one
  MusicBrainz lookup, never blocks the download itself since it's its own
  task.
- musicbrainz_sweep_task: a slow background sweep for tracks that were
  never checked at all (e.g. downloaded before this feature existed, or
  ones whose enrich_track_task never got the chance to run). Runs
  periodically via Celery Beat, but only does real work when no download
  batch is currently active and only processes a small, admin-configurable
  batch each time — see SiteSettings.musicbrainz_sweep_batch_size.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.musicbrainz.services import auto_enrich_track

logger = logging.getLogger(__name__)


@shared_task
def enrich_track_task(track_file_id: int) -> None:
    from apps.core.models import SiteSettings
    from apps.library.models import TrackFile

    if not SiteSettings.load().musicbrainz_enabled:
        return
    try:
        track_file = TrackFile.objects.get(pk=track_file_id, removed_at__isnull=True)
    except TrackFile.DoesNotExist:
        return
    auto_enrich_track(track_file)


def _downloads_are_active() -> bool:
    """True if any DownloadBatch still has work left to do.

    The sweep must never compete with real downloads — for a worker slot,
    for MusicBrainz's own shared ~1req/s budget, or for the audio
    provider's attention — so it only ever runs while this is False."""
    from apps.downloader.models import BatchStatus, DownloadBatch

    active_statuses = [BatchStatus.PENDING, BatchStatus.RESOLVING, BatchStatus.RUNNING]
    return DownloadBatch.objects.filter(status__in=active_statuses).exists()


@shared_task
def musicbrainz_sweep_task() -> dict:
    from apps.core.models import SiteSettings
    from apps.library.models import TrackFile

    site_settings = SiteSettings.load()
    if not site_settings.musicbrainz_enabled:
        return {"skipped": "musicbrainz_disabled", "checked": 0}
    if _downloads_are_active():
        return {"skipped": "downloads_active", "checked": 0}

    batch_size = site_settings.musicbrainz_sweep_batch_size
    tracks = list(
        TrackFile.objects.filter(musicbrainz_checked_at__isnull=True, removed_at__isnull=True)
        .order_by("created_at")[:batch_size]
    )

    checked = 0
    matched = 0
    for track_file in tracks:
        if auto_enrich_track(track_file):
            matched += 1
        checked += 1

    if checked:
        logger.info("MusicBrainz sweep: checked %d track(s), %d matched", checked, matched)
    return {"checked": checked, "matched": matched}
