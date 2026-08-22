"""Scheduled cleanup (CLAUDE.md #23).

Every function here is conservative on purpose: it only touches state that
is unambiguously stale (abandoned in-progress items, orphaned temp
directories, long-expired shared links). It never deletes a TrackFile or
the underlying music file — "Never automatically delete final music files
merely because a database entry disappeared" (CLAUDE.md #23).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.downloader.models import BatchStatus, DownloadBatch, DownloadItem, ItemStatus
from apps.sharing.models import SharedImportLink

logger = logging.getLogger(__name__)

_IN_PROGRESS_STATUSES = [ItemStatus.PENDING, ItemStatus.QUEUED, ItemStatus.DOWNLOADING, ItemStatus.PROCESSING]
_UNRESOLVED_BATCH_STATUSES = [BatchStatus.PENDING, BatchStatus.RESOLVING]


@dataclass(frozen=True)
class CleanupReport:
    abandoned_items: int = 0
    abandoned_batches: int = 0
    orphaned_temp_dirs: int = 0
    expired_shared_links: int = 0


def cleanup_abandoned_items(*, max_age_seconds: int) -> int:
    """Fail any DownloadItem that has sat in an in-progress status for
    longer than `max_age_seconds` — almost always a worker that crashed or
    was killed mid-task rather than one that is still legitimately
    running (CLAUDE.md #16: design for interrupted downloads)."""
    cutoff = timezone.now() - timezone.timedelta(seconds=max_age_seconds)
    stuck = DownloadItem.objects.filter(status__in=_IN_PROGRESS_STATUSES, created_at__lt=cutoff)

    count = 0
    for item in stuck:
        item.status = ItemStatus.FAILED
        item.error_message = "Abandoned: exceeded maximum processing time (worker likely restarted)."
        item.current_stage = ""
        item.save(update_fields=["status", "error_message", "current_stage"])
        item.batch.recompute_status()
        count += 1

    if count:
        logger.info("Cleanup: marked %d abandoned download item(s) as failed", count)
    return count


def cleanup_abandoned_batches(*, max_age_seconds: int) -> int:
    """Fail any DownloadBatch stuck in PENDING/RESOLVING with zero items
    for longer than `max_age_seconds` — resolve_batch_task never
    finished (or never even ran), almost always because a worker was
    replaced mid-task before it could create any DownloadItem rows
    (reproduced for real; see the CELERY_TASK_ACKS_LATE comment in
    config/settings/base.py, the actual fix — this is only the safety
    net for tasks lost before that existed, or any other way this could
    still happen).

    Deliberately narrower than cleanup_abandoned_items: once a batch has
    at least one item, that function already covers it — failing its
    stuck items and recomputing the batch status from them. This only
    catches a batch that never got that far at all.
    """
    cutoff = timezone.now() - timezone.timedelta(seconds=max_age_seconds)
    stuck = DownloadBatch.objects.filter(
        status__in=_UNRESOLVED_BATCH_STATUSES, created_at__lt=cutoff, items__isnull=True,
    )

    count = 0
    for batch in stuck:
        batch.status = BatchStatus.FAILED
        batch.error_summary = "Abandoned: never finished resolving (worker likely restarted mid-task)."
        batch.save(update_fields=["status", "error_summary", "updated_at"])
        count += 1

    if count:
        logger.info("Cleanup: marked %d abandoned batch(es) with no items as failed", count)
    return count


def cleanup_orphaned_temp_dirs() -> int:
    """Remove `item-<id>` directories under DOWNLOAD_TEMP_ROOT that don't
    correspond to any still-in-progress DownloadItem.

    Normal completion/failure already cleans up after itself (see
    apps.downloader.tasks); this only catches leftovers from a worker
    that was killed before it could run its own cleanup.
    """
    root = Path(settings.DOWNLOAD_TEMP_ROOT)
    if not root.exists():
        return 0

    in_progress_ids = set(
        DownloadItem.objects.filter(status__in=_IN_PROGRESS_STATUSES).values_list("id", flat=True)
    )

    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir() or not entry.name.startswith("item-"):
            continue
        try:
            item_id = int(entry.name.removeprefix("item-"))
        except ValueError:
            continue
        if item_id in in_progress_ids:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1

    if removed:
        logger.info("Cleanup: removed %d orphaned temp download directory(ies)", removed)
    return removed


def cleanup_expired_shared_links(*, retention_days: int) -> int:
    """Delete SharedImportLink rows that expired more than
    `retention_days` ago. Deletion here is fine (unlike TrackFile/library
    data) — an expired link carries no library data of its own, only
    usage bookkeeping, and DownloadBatch.shared_link is SET_NULL so past
    batches stay intact."""
    cutoff = timezone.now() - timezone.timedelta(days=retention_days)
    queryset = SharedImportLink.objects.filter(expires_at__lt=cutoff)
    count = queryset.count()
    if count:
        queryset.delete()
        logger.info("Cleanup: deleted %d long-expired shared link(s)", count)
    return count


def run_all_cleanup_tasks() -> CleanupReport:
    return CleanupReport(
        abandoned_items=cleanup_abandoned_items(
            max_age_seconds=settings.SPOTDL_DOWNLOAD_TIMEOUT * 2,
        ),
        abandoned_batches=cleanup_abandoned_batches(
            # SPOTDL_RESOLVE_TIMEOUT, not SPOTDL_DOWNLOAD_TIMEOUT: a batch sits in
            # PENDING/RESOLVING for as long as resolve_source() is allowed to run
            # (apps/downloader/services/spotdl.py), which is the resolve timeout, not
            # the per-track download one. Using the download timeout here would let
            # this cleanup task fail a batch that resolve_source() itself hasn't
            # given up on yet, for a legitimately large playlist.
            max_age_seconds=settings.SPOTDL_RESOLVE_TIMEOUT * 2,
        ),
        orphaned_temp_dirs=cleanup_orphaned_temp_dirs(),
        expired_shared_links=cleanup_expired_shared_links(
            retention_days=settings.SHARED_LINK_CLEANUP_RETENTION_DAYS,
        ),
    )
