from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BatchStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RESOLVING = "resolving", _("Resolving sources")
    RUNNING = "running", _("Running")
    COMPLETED = "completed", _("Completed")
    COMPLETED_WITH_ERRORS = "completed_with_errors", _("Completed with errors")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class SourceType(models.TextChoices):
    TRACK = "track", _("Track")
    ALBUM = "album", _("Album")
    PLAYLIST = "playlist", _("Playlist")
    ARTIST = "artist", _("Artist")
    MIXED = "mixed", _("Mixed")


class DownloadBatch(models.Model):
    """One user submission: one or more source URLs queued together.

    A batch stays useful even if some of its items fail (CLAUDE.md #5) —
    per-item outcomes live on DownloadItem, the batch just aggregates them.
    """

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="download_batches",
    )
    # The shared-import-link submission this batch originated from, if any.
    shared_link = models.ForeignKey(
        "sharing.SharedImportLink", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="download_batches",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    source_urls = models.JSONField(help_text="Raw URLs as submitted, before resolution.")
    source_type = models.CharField(max_length=16, choices=SourceType.choices, default=SourceType.MIXED)
    title = models.CharField(max_length=255, blank=True)

    status = models.CharField(max_length=24, choices=BatchStatus.choices, default=BatchStatus.PENDING)
    total_items = models.PositiveIntegerField(default=0)
    completed_items = models.PositiveIntegerField(default=0)
    failed_items = models.PositiveIntegerField(default=0)
    cancelled_items = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)
    # Set once a "batch finished" notification email has been sent, so a
    # later recompute_status() call (e.g. after a late retry) never sends
    # a second email for the same batch (CLAUDE.md #17: don't send email
    # per track, and don't double-notify either).
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_by", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.title or f"Batch #{self.pk}"

    @property
    def is_finished(self) -> bool:
        return self.status in {
            BatchStatus.COMPLETED,
            BatchStatus.COMPLETED_WITH_ERRORS,
            BatchStatus.FAILED,
            BatchStatus.CANCELLED,
        }

    def recompute_status(self, *, save: bool = True) -> None:
        """Derive batch status/counters from its items' current state.

        Called after every item state transition so the batch never drifts
        out of sync with its items, even across worker restarts.
        """
        counts = self.items.values("status").annotate(n=models.Count("id"))
        by_status = {row["status"]: row["n"] for row in counts}

        self.total_items = self.items.count()
        self.completed_items = by_status.get(ItemStatus.COMPLETED, 0) + by_status.get(
            ItemStatus.DUPLICATE_SKIPPED, 0
        )
        self.failed_items = by_status.get(ItemStatus.FAILED, 0)
        self.cancelled_items = by_status.get(ItemStatus.CANCELLED, 0)

        pending_like = {ItemStatus.PENDING, ItemStatus.QUEUED, ItemStatus.DOWNLOADING, ItemStatus.PROCESSING}
        still_pending = any(by_status.get(s, 0) for s in pending_like)

        if self.total_items == 0:
            self.status = BatchStatus.PENDING
        elif still_pending:
            self.status = BatchStatus.RUNNING
        elif self.cancelled_items == self.total_items:
            self.status = BatchStatus.CANCELLED
        elif self.failed_items and self.completed_items:
            self.status = BatchStatus.COMPLETED_WITH_ERRORS
        elif self.failed_items and not self.completed_items:
            self.status = BatchStatus.FAILED
        else:
            self.status = BatchStatus.COMPLETED

        if save:
            self.save(update_fields=[
                "total_items", "completed_items", "failed_items",
                "cancelled_items", "status", "updated_at",
            ])
            if self.is_finished and self.notified_at is None:
                self._notify_finished()

    def _notify_finished(self) -> None:
        """Send the batch-completed/failed notification email exactly
        once, on the transition into a finished status."""
        from apps.core.notifications import send_batch_finished_email

        self.notified_at = timezone.now()
        self.save(update_fields=["notified_at"])
        send_batch_finished_email(self)


class ItemStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    QUEUED = "queued", _("Queued")
    DOWNLOADING = "downloading", _("Downloading")
    PROCESSING = "processing", _("Processing")
    COMPLETED = "completed", _("Completed")
    DUPLICATE_SKIPPED = "duplicate_skipped", _("Skipped (duplicate)")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


class DuplicateStatus(models.TextChoices):
    UNKNOWN = "unknown", _("Not checked")
    NEW = "new", _("New track")
    EXACT_DUPLICATE = "exact_duplicate", _("Exact duplicate")
    PROBABLE_DUPLICATE = "probable_duplicate", _("Probable duplicate")


class DownloadItem(models.Model):
    """A single track within a DownloadBatch."""

    batch = models.ForeignKey(DownloadBatch, on_delete=models.CASCADE, related_name="items")
    result_file = models.ForeignKey(
        "library.TrackFile", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="download_items",
    )

    source_url = models.URLField(max_length=2048)
    source_identifier = models.CharField(
        max_length=64, blank=True, db_index=True,
        help_text="e.g. the Spotify track ID, stable across re-imports.",
    )

    title = models.CharField(max_length=255, blank=True)
    artist = models.CharField(max_length=255, blank=True)
    album = models.CharField(max_length=255, blank=True)
    album_artist = models.CharField(max_length=255, blank=True)
    playlist_name = models.CharField(max_length=255, blank=True)
    playlist_position = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=ItemStatus.choices, default=ItemStatus.PENDING)
    duplicate_status = models.CharField(
        max_length=20, choices=DuplicateStatus.choices, default=DuplicateStatus.UNKNOWN
    )
    # Which audio provider actually supplied this attempt (see
    # apps.downloader.tasks.pick_audio_providers()) — copied onto the
    # resulting TrackFile at finalization so probable-duplicate handling
    # can use it (apps.library.services._should_replace_with_better_version).
    # Best-effort: when a retry's fallback tier lists more than one
    # provider for a single spotDL invocation, this only ever records the
    # first of them, since spotDL's own output doesn't reliably say which
    # one actually matched.
    source_provider = models.CharField(max_length=20, blank=True)
    progress = models.PositiveSmallIntegerField(default=0, help_text="0-100")
    current_stage = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)

    # The exact spotDL command last run for this item, and its captured
    # stdout/stderr (tail — see SPOTDL_OUTPUT_TAIL_CHARS). Staff-only
    # visibility (admin, and a collapsible panel on the batch page) for
    # troubleshooting — never shown to non-staff users, since command
    # output can include local filesystem paths.
    last_command = models.TextField(blank=True)
    last_output = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["playlist_position", "id"]
        indexes = [
            models.Index(fields=["batch", "status"]),
            models.Index(fields=["source_identifier"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return self.title or self.source_url
