import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.core.cleanup import (
    cleanup_abandoned_batches,
    cleanup_abandoned_items,
    cleanup_expired_shared_links,
    cleanup_orphaned_temp_dirs,
)
from apps.downloader.models import BatchStatus, DownloadBatch, DownloadItem, ItemStatus
from apps.sharing.models import SharedImportLink

pytestmark = pytest.mark.django_db


def make_batch():
    return DownloadBatch.objects.create(source_urls=["https://open.spotify.com/track/x"], status=BatchStatus.RUNNING)


def make_item(batch, status, created_at=None):
    item = DownloadItem.objects.create(batch=batch, source_url="https://open.spotify.com/track/x", status=status)
    if created_at is not None:
        DownloadItem.objects.filter(pk=item.pk).update(created_at=created_at)
        item.refresh_from_db()
    return item


class TestCleanupAbandonedItems:
    def test_marks_old_in_progress_item_as_failed(self):
        batch = make_batch()
        old = timezone.now() - timezone.timedelta(hours=2)
        item = make_item(batch, ItemStatus.DOWNLOADING, created_at=old)

        count = cleanup_abandoned_items(max_age_seconds=3600)

        assert count == 1
        item.refresh_from_db()
        assert item.status == ItemStatus.FAILED
        assert "Abandoned" in item.error_message

    def test_leaves_recent_in_progress_item_alone(self):
        batch = make_batch()
        item = make_item(batch, ItemStatus.DOWNLOADING)  # created_at = now

        count = cleanup_abandoned_items(max_age_seconds=3600)

        assert count == 0
        item.refresh_from_db()
        assert item.status == ItemStatus.DOWNLOADING

    def test_leaves_finished_items_alone(self):
        batch = make_batch()
        old = timezone.now() - timezone.timedelta(hours=2)
        item = make_item(batch, ItemStatus.COMPLETED, created_at=old)

        count = cleanup_abandoned_items(max_age_seconds=3600)

        assert count == 0
        item.refresh_from_db()
        assert item.status == ItemStatus.COMPLETED

    def test_updates_batch_status_after_marking_failed(self):
        batch = make_batch()
        old = timezone.now() - timezone.timedelta(hours=2)
        make_item(batch, ItemStatus.DOWNLOADING, created_at=old)

        cleanup_abandoned_items(max_age_seconds=3600)

        batch.refresh_from_db()
        assert batch.status == BatchStatus.FAILED


def make_stuck_batch(status, created_at=None, with_item=False):
    batch = DownloadBatch.objects.create(source_urls=["https://open.spotify.com/track/x"], status=status)
    if created_at is not None:
        DownloadBatch.objects.filter(pk=batch.pk).update(created_at=created_at)
        batch.refresh_from_db()
    if with_item:
        DownloadItem.objects.create(batch=batch, source_url="https://open.spotify.com/track/x")
    return batch


class TestCleanupAbandonedBatches:
    def test_marks_old_empty_pending_batch_as_failed(self):
        old = timezone.now() - timezone.timedelta(hours=2)
        batch = make_stuck_batch(BatchStatus.PENDING, created_at=old)

        count = cleanup_abandoned_batches(max_age_seconds=3600)

        assert count == 1
        batch.refresh_from_db()
        assert batch.status == BatchStatus.FAILED
        assert "Abandoned" in batch.error_summary

    def test_marks_old_empty_resolving_batch_as_failed(self):
        old = timezone.now() - timezone.timedelta(hours=2)
        batch = make_stuck_batch(BatchStatus.RESOLVING, created_at=old)

        count = cleanup_abandoned_batches(max_age_seconds=3600)

        assert count == 1
        batch.refresh_from_db()
        assert batch.status == BatchStatus.FAILED

    def test_leaves_recent_empty_batch_alone(self):
        batch = make_stuck_batch(BatchStatus.RESOLVING)  # created_at = now

        count = cleanup_abandoned_batches(max_age_seconds=3600)

        assert count == 0
        batch.refresh_from_db()
        assert batch.status == BatchStatus.RESOLVING

    def test_leaves_old_batch_with_items_alone(self):
        # Already covered once it has items: cleanup_abandoned_items
        # fails the stuck items themselves and recomputes the batch
        # status from those — this function only needs the zero-item gap.
        old = timezone.now() - timezone.timedelta(hours=2)
        batch = make_stuck_batch(BatchStatus.RESOLVING, created_at=old, with_item=True)

        count = cleanup_abandoned_batches(max_age_seconds=3600)

        assert count == 0
        batch.refresh_from_db()
        assert batch.status == BatchStatus.RESOLVING

    def test_leaves_old_finished_batch_alone(self):
        old = timezone.now() - timezone.timedelta(hours=2)
        batch = make_stuck_batch(BatchStatus.COMPLETED, created_at=old)

        count = cleanup_abandoned_batches(max_age_seconds=3600)

        assert count == 0
        batch.refresh_from_db()
        assert batch.status == BatchStatus.COMPLETED


class TestCleanupOrphanedTempDirs:
    def test_removes_directory_for_finished_item(self, tmp_path, settings):
        settings.DOWNLOAD_TEMP_ROOT = tmp_path
        batch = make_batch()
        item = make_item(batch, ItemStatus.COMPLETED)
        job_dir = tmp_path / f"item-{item.id}"
        job_dir.mkdir()
        (job_dir / "leftover.mp3").write_bytes(b"x")

        removed = cleanup_orphaned_temp_dirs()

        assert removed == 1
        assert not job_dir.exists()

    def test_keeps_directory_for_in_progress_item(self, tmp_path, settings):
        settings.DOWNLOAD_TEMP_ROOT = tmp_path
        batch = make_batch()
        item = make_item(batch, ItemStatus.DOWNLOADING)
        job_dir = tmp_path / f"item-{item.id}"
        job_dir.mkdir()

        removed = cleanup_orphaned_temp_dirs()

        assert removed == 0
        assert job_dir.exists()

    def test_ignores_unrelated_files_and_dirs(self, tmp_path, settings):
        settings.DOWNLOAD_TEMP_ROOT = tmp_path
        (tmp_path / "not-an-item-dir").mkdir()
        (tmp_path / "stray-file.txt").write_bytes(b"x")

        removed = cleanup_orphaned_temp_dirs()
        assert removed == 0

    def test_missing_temp_root_is_a_noop(self, tmp_path, settings):
        settings.DOWNLOAD_TEMP_ROOT = tmp_path / "does-not-exist"
        assert cleanup_orphaned_temp_dirs() == 0


class TestCleanupExpiredSharedLinks:
    def test_deletes_long_expired_link(self):
        admin = User.objects.create_user(username="admin", password="x", is_staff=True)
        link, _token = SharedImportLink.generate(
            created_by=admin, ttl_hours=1, maximum_uses=1,
        )
        SharedImportLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timezone.timedelta(days=60)
        )

        count = cleanup_expired_shared_links(retention_days=30)

        assert count == 1
        assert not SharedImportLink.objects.filter(pk=link.pk).exists()

    def test_keeps_recently_expired_link(self):
        admin = User.objects.create_user(username="admin", password="x", is_staff=True)
        link, _token = SharedImportLink.generate(
            created_by=admin, ttl_hours=1, maximum_uses=1,
        )
        SharedImportLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timezone.timedelta(days=1)
        )

        count = cleanup_expired_shared_links(retention_days=30)

        assert count == 0
        assert SharedImportLink.objects.filter(pk=link.pk).exists()

    def test_does_not_touch_batches_referencing_deleted_link(self):
        admin = User.objects.create_user(username="admin", password="x", is_staff=True)
        link, _token = SharedImportLink.generate(
            created_by=admin, ttl_hours=1, maximum_uses=1,
        )
        batch = DownloadBatch.objects.create(
            shared_link=link, source_urls=["https://open.spotify.com/track/x"],
        )
        SharedImportLink.objects.filter(pk=link.pk).update(
            expires_at=timezone.now() - timezone.timedelta(days=60)
        )

        cleanup_expired_shared_links(retention_days=30)

        batch.refresh_from_db()
        assert batch.shared_link_id is None  # SET_NULL, batch itself survives
