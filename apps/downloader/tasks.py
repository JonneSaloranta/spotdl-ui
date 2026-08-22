"""Celery tasks for the download pipeline (CLAUDE.md #5).

State lives entirely in PostgreSQL (DownloadBatch/DownloadItem), not in
worker memory or Celery's result backend, so a worker restart never loses
track of in-flight work: any task can be safely re-delivered because every
handler starts by re-reading current state from the database and treating
already-finished items as a no-op (CLAUDE.md #9/#16).
"""

from __future__ import annotations

import logging
import shlex
import shutil
from pathlib import Path

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.core.models import SiteSettings
from apps.downloader.models import (
    BatchStatus,
    DownloadBatch,
    DownloadItem,
    DuplicateStatus,
    ItemStatus,
)
from apps.downloader.services import spotdl
from apps.library.services import finalize_download, find_existing_download
from apps.musicbrainz.tasks import enrich_track_task

logger = logging.getLogger(__name__)

_FINISHED_ITEM_STATUSES = {ItemStatus.COMPLETED, ItemStatus.DUPLICATE_SKIPPED, ItemStatus.CANCELLED}


def pick_audio_providers(retry_count: int, site_settings: SiteSettings | None = None) -> list[str] | None:
    """Which audio provider(s) to use for this attempt.

    Admin-editable (the /settings/ page, or Django admin), not an env var —
    unlike most spotDL configuration, this is something admins were asking
    to change often enough (YouTube's anti-bot blocking varies by
    deployment) that requiring an env var edit + container restart was
    real friction. See apps.core.models.SiteSettings for the fields and
    docs/TROUBLESHOOTING.md for the underlying YouTube-blocking issue.

    Tries `primary_audio_provider` for the first `primary_provider_attempts`
    attempts, then switches to `fallback_audio_providers` for every
    attempt after that.

    `retry_count` is 0 on the first attempt, matching DownloadItem.retry_count
    before it's incremented for *this* attempt's own failure handling.
    """
    site_settings = site_settings or SiteSettings.load()
    attempt_number = retry_count + 1
    if attempt_number <= site_settings.primary_provider_attempts:
        return [site_settings.primary_audio_provider] if site_settings.primary_audio_provider else None
    return site_settings.fallback_audio_providers_list() or None


def queue_batch_resolve(batch: DownloadBatch) -> None:
    """Queue `resolve_batch_task` for `batch`, with a per-task Celery time
    limit sized for *this* batch instead of relying on the global
    CELERY_TASK_TIME_LIMIT.

    resolve_batch_task loops over every one of the batch's source URLs
    sequentially, and each one is individually allowed up to
    SPOTDL_RESOLVE_TIMEOUT before spotdl.resolve_source() gives up on it
    (see apps/downloader/services/spotdl.py) — so a batch with several
    large playlists can legitimately need several multiples of that
    before it's done with all of them, only failing the slow ones
    individually rather than the whole batch. The global
    CELERY_TASK_TIME_LIMIT (config/settings/base.py, 30 min by default)
    is sized for ordinary single-track tasks like download_item_task, and
    would otherwise SIGKILL a resolve that's still legitimately working
    through multiple large playlists — with no clean error recorded,
    unlike a graceful SpotDLTimeoutError (reproduced for real: raising
    SPOTDL_RESOLVE_TIMEOUT past 600s to accommodate one large playlist,
    without also raising this, would have made *that* case worse, not
    better, for anyone submitting more than one such playlist at a time).
    """
    per_source = settings.SPOTDL_RESOLVE_TIMEOUT + 60  # small buffer over our own graceful timeout
    time_limit = max(per_source, len(batch.source_urls) * per_source)
    resolve_batch_task.apply_async(args=[batch.id], time_limit=time_limit, soft_time_limit=time_limit - 30)


@shared_task(bind=True)
def resolve_batch_task(self, batch_id: int) -> None:
    """Resolve a batch's source URLs into DownloadItem rows and queue them.

    Idempotent: re-running against an already-resolved batch only queues
    items that don't already exist (matched by source_url within the
    batch), so redelivery after a worker crash never double-imports.

    Always queue this through queue_batch_resolve() above, not
    .delay()/.apply_async() directly — see its docstring for why the
    time limit needs to be sized per-batch.
    """
    try:
        batch = DownloadBatch.objects.get(pk=batch_id)
    except DownloadBatch.DoesNotExist:
        logger.warning("resolve_batch_task: batch %s no longer exists", batch_id)
        return

    if batch.status not in {BatchStatus.PENDING, BatchStatus.RESOLVING}:
        logger.info("resolve_batch_task: batch %s already past resolving (%s)", batch_id, batch.status)
        return

    batch.status = BatchStatus.RESOLVING
    batch.save(update_fields=["status", "updated_at"])

    errors: list[str] = []
    any_resolved = False

    total_sources = len(batch.source_urls)
    for index, raw_url in enumerate(batch.source_urls, start=1):
        logger.info(
            "resolve_batch_task: batch %s resolving source %d/%d: %s",
            batch_id, index, total_sources, raw_url,
        )
        try:
            tracks = spotdl.resolve_source(raw_url)
        except spotdl.SpotDLError as exc:
            logger.warning("Failed to resolve %s for batch %s: %s", raw_url, batch_id, exc)
            errors.append(f"{raw_url}: {exc}")
            continue

        if not tracks:
            errors.append(f"{raw_url}: no tracks found")
            continue

        if len(tracks) > settings.MAX_TRACKS_PER_SOURCE:
            errors.append(
                f"{raw_url}: {len(tracks)} tracks found, only the first "
                f"{settings.MAX_TRACKS_PER_SOURCE} were queued (per-source limit)."
            )
            tracks = tracks[: settings.MAX_TRACKS_PER_SOURCE]

        any_resolved = True
        for track in tracks:
            item, created = DownloadItem.objects.get_or_create(
                batch=batch,
                source_url=track.source_url,
                defaults={
                    "source_identifier": track.source_identifier,
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "album_artist": track.album_artist,
                    "playlist_name": track.playlist_name,
                    "playlist_position": track.playlist_position,
                },
            )
            if created:
                existing = find_existing_download(
                    source_identifier=track.source_identifier, source_url=track.source_url,
                )
                if existing is not None:
                    # Already downloaded from this exact source in a past import (a
                    # different playlist containing the same track, someone
                    # resubmitting the same link, ...) — skip spending a download
                    # attempt (and provider quota) re-confirming what we already
                    # know, and point straight at the file we already have.
                    logger.info(
                        "resolve_batch_task: item %s (%s) already downloaded as track %s; skipping",
                        item.id, track.source_url, existing.id,
                    )
                    item.status = ItemStatus.DUPLICATE_SKIPPED
                    item.result_file = existing
                    item.duplicate_status = DuplicateStatus.EXACT_DUPLICATE
                    item.progress = 100
                    item.completed_at = timezone.now()
                    item.save(update_fields=[
                        "status", "result_file", "duplicate_status", "progress", "completed_at",
                    ])
                else:
                    download_item_task.delay(item.id)

    batch.error_summary = "\n".join(errors)[:5000]

    if not any_resolved:
        # No items were ever created, so recompute_status()'s "zero items"
        # case (which means PENDING, i.e. not started yet) would otherwise
        # incorrectly overwrite this FAILED status — set it explicitly and
        # stop here instead.
        batch.status = BatchStatus.FAILED
        batch.save(update_fields=["error_summary", "status", "updated_at"])
        return

    batch.status = BatchStatus.RUNNING
    batch.save(update_fields=["error_summary", "status", "updated_at"])
    batch.recompute_status()


@shared_task(bind=True, max_retries=None)  # per-item retry budget is enforced manually via retry_count
def download_item_task(self, item_id: int) -> None:
    """Download, hash, deduplicate, and finalize a single track."""
    try:
        item = DownloadItem.objects.select_related("batch").get(pk=item_id)
    except DownloadItem.DoesNotExist:
        logger.warning("download_item_task: item %s no longer exists", item_id)
        return

    if item.status in _FINISHED_ITEM_STATUSES:
        return  # already handled — safe no-op on task redelivery

    job_dir = Path(settings.DOWNLOAD_TEMP_ROOT) / f"item-{item.id}"
    audio_providers = pick_audio_providers(item.retry_count)

    # Recorded before running, independent of the outcome, so staff can
    # always see the exact command that was (or is about to be) invoked —
    # including for the exception paths below where no DownloadOutcome
    # (and therefore no captured stdout/stderr) is ever produced.
    item.last_command = shlex.join(
        spotdl.build_download_command(item.source_url, job_dir, audio_providers=audio_providers)
    )

    # Best-effort: the fallback tier can list more than one provider for
    # a single spotDL invocation (--audio p1 p2), and spotDL's own output
    # doesn't reliably say which one actually matched — recording just
    # the first is close enough for the auto-replace heuristic that
    # reads it later (apps.library.services._should_replace_with_better_version)
    # without needing to parse stdout for it.
    item.source_provider = audio_providers[0] if audio_providers else ""
    item.status = ItemStatus.DOWNLOADING
    item.current_stage = "downloading via " + (", ".join(audio_providers) if audio_providers else "default provider")
    item.started_at = item.started_at or timezone.now()
    item.progress = 10
    item.save(update_fields=[
        "status", "current_stage", "started_at", "progress", "last_command", "source_provider",
    ])

    def should_cancel() -> bool:
        return DownloadItem.objects.filter(pk=item.id, status=ItemStatus.CANCELLED).exists()

    def on_progress(percent: int, stage: str) -> None:
        # .update() rather than item.save(): this runs synchronously
        # inside download_track() (see spotdl._run()'s own docstring for
        # why that's safe), well before the in-memory `item` object's
        # other fields (result_file, duplicate_status, ...) are known —
        # updating just these two columns avoids overwriting anything
        # else with stale in-memory values, and every code path below
        # that matters (success or failure) sets progress/current_stage
        # again explicitly anyway, so there's no risk of this being the
        # last word if something later disagrees.
        DownloadItem.objects.filter(pk=item.id).update(progress=percent, current_stage=stage)

    try:
        outcome = spotdl.download_track(
            item.source_url, job_dir, should_cancel=should_cancel, audio_providers=audio_providers,
            on_progress=on_progress,
        )
    except spotdl.SpotDLCancelledError:
        _mark_cancelled(item)
        return
    except spotdl.SpotDLError as exc:
        _handle_failure(self, item, str(exc))
        return
    finally:
        pass  # job_dir is cleaned up below, after any successful finalize() move

    item.last_output = _format_output(outcome.stdout_tail, outcome.stderr_tail)
    item.save(update_fields=["last_output"])

    if not outcome.success:
        _handle_failure(self, item, outcome.error_message)
        shutil.rmtree(job_dir, ignore_errors=True)
        return

    item.current_stage = "processing"
    item.progress = 80
    item.save(update_fields=["current_stage", "progress"])

    try:
        result = finalize_download(outcome.file_path, item=item)
    except Exception:
        logger.exception("Finalization failed for item %s", item.id)
        _handle_failure(self, item, "Failed to process the downloaded file.")
        return
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)

    item.result_file = result.track_file
    item.duplicate_status = result.duplicate_status
    item.status = ItemStatus.DUPLICATE_SKIPPED if result.is_duplicate_skip else ItemStatus.COMPLETED
    item.progress = 100
    item.current_stage = ""
    item.completed_at = timezone.now()
    item.save(update_fields=[
        "result_file", "duplicate_status", "status", "progress", "current_stage", "completed_at",
    ])
    item.batch.recompute_status()

    if not result.is_duplicate_skip:
        # A duplicate skip means result.track_file is an *existing* file
        # (or wasn't newly stored at all) that would already have been
        # enriched on its own first download, if ever — only a genuinely
        # new file needs its own lookup. Its own task, not inline here, so
        # a MusicBrainz hiccup can never affect a download that already
        # succeeded (CLAUDE.md #16).
        enrich_track_task.delay(result.track_file.id)


def _format_output(stdout: str, stderr: str) -> str:
    parts = []
    if stdout.strip():
        parts.append(f"--- stdout ---\n{stdout.strip()}")
    if stderr.strip():
        parts.append(f"--- stderr ---\n{stderr.strip()}")
    return "\n\n".join(parts)


def _mark_cancelled(item: DownloadItem) -> None:
    item.status = ItemStatus.CANCELLED
    item.cancelled_at = timezone.now()
    item.current_stage = ""
    item.save(update_fields=["status", "cancelled_at", "current_stage"])
    item.batch.recompute_status()


def _handle_failure(task, item: DownloadItem, message: str) -> None:
    """Record a failure and either schedule a retry or give up.

    Retry state (retry_count) lives on the DownloadItem itself rather than
    relying on Celery's own retry bookkeeping, so it survives independently
    of the task/broker and is visible directly in the admin/UI.
    """
    item.retry_count += 1
    item.error_message = (message or "")[:2000]

    if item.retry_count <= settings.SPOTDL_MAX_RETRIES:
        item.status = ItemStatus.PENDING
        # Reset to 0 (not left at whatever it was mid-download, e.g. 10%)
        # and say plainly that a retry is scheduled — otherwise this looks
        # indistinguishable from a genuinely stuck download while it waits
        # out the backoff, which can be minutes (CLAUDE.md #12: show
        # current stage). Matches the existing convention of "downloading"/
        # "processing" as plain, untranslated stage labels.
        countdown = min(60 * item.retry_count, 600)
        item.progress = 0
        item.current_stage = f"retrying in {countdown}s (attempt {item.retry_count}/{settings.SPOTDL_MAX_RETRIES})"
        item.save(update_fields=["retry_count", "error_message", "status", "current_stage", "progress"])
        raise task.retry(countdown=countdown)

    item.status = ItemStatus.FAILED
    item.current_stage = ""
    item.save(update_fields=["retry_count", "error_message", "status", "current_stage"])
    item.batch.recompute_status()
