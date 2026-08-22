import pytest

from apps.downloader.models import BatchStatus, DownloadBatch, DownloadItem, ItemStatus

pytestmark = pytest.mark.django_db


def make_batch(**kwargs):
    return DownloadBatch.objects.create(source_urls=["https://open.spotify.com/track/x"], **kwargs)


def make_item(batch, status=ItemStatus.PENDING, **kwargs):
    return DownloadItem.objects.create(
        batch=batch, source_url="https://open.spotify.com/track/x", status=status, **kwargs
    )


class TestRecomputeStatus:
    def test_empty_batch_is_pending(self):
        batch = make_batch()
        batch.recompute_status()
        assert batch.status == BatchStatus.PENDING

    def test_batch_with_pending_items_is_running(self):
        batch = make_batch()
        make_item(batch, status=ItemStatus.PENDING)
        batch.recompute_status()
        assert batch.status == BatchStatus.RUNNING

    def test_batch_fully_completed(self):
        batch = make_batch()
        make_item(batch, status=ItemStatus.COMPLETED)
        make_item(batch, status=ItemStatus.COMPLETED)
        batch.recompute_status()
        assert batch.status == BatchStatus.COMPLETED
        assert batch.completed_items == 2

    def test_batch_completed_with_errors_when_mixed(self):
        batch = make_batch()
        make_item(batch, status=ItemStatus.COMPLETED)
        make_item(batch, status=ItemStatus.FAILED)
        batch.recompute_status()
        assert batch.status == BatchStatus.COMPLETED_WITH_ERRORS

    def test_batch_fully_failed(self):
        batch = make_batch()
        make_item(batch, status=ItemStatus.FAILED)
        batch.recompute_status()
        assert batch.status == BatchStatus.FAILED

    def test_batch_fully_cancelled(self):
        batch = make_batch()
        make_item(batch, status=ItemStatus.CANCELLED)
        make_item(batch, status=ItemStatus.CANCELLED)
        batch.recompute_status()
        assert batch.status == BatchStatus.CANCELLED

    def test_duplicate_skipped_counts_as_completed(self):
        batch = make_batch()
        make_item(batch, status=ItemStatus.DUPLICATE_SKIPPED)
        batch.recompute_status()
        assert batch.status == BatchStatus.COMPLETED
        assert batch.completed_items == 1

    def test_stays_running_while_any_item_still_in_progress(self):
        batch = make_batch()
        make_item(batch, status=ItemStatus.COMPLETED)
        make_item(batch, status=ItemStatus.DOWNLOADING)
        batch.recompute_status()
        assert batch.status == BatchStatus.RUNNING

    def test_is_finished_property(self):
        batch = make_batch(status=BatchStatus.COMPLETED)
        assert batch.is_finished is True
        batch.status = BatchStatus.RUNNING
        assert batch.is_finished is False
