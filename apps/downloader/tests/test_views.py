from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.downloader.models import BatchStatus, DownloadBatch, DownloadItem, ItemStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def user():
    return User.objects.create_user(username="alice", password="x")


@pytest.fixture
def other_user():
    return User.objects.create_user(username="bob", password="x")


@pytest.fixture
def logged_in_client(user):
    c = Client()
    c.force_login(user)
    return c


def test_submit_requires_login(client: Client):
    resp = client.post(reverse("downloader:submit"), {"urls": "https://open.spotify.com/track/a"})
    assert resp.status_code == 302
    assert "/accounts/login/" in resp.url


def test_submit_creates_batch_and_queues_resolution(logged_in_client, user):
    with patch("apps.downloader.views.queue_batch_resolve") as mock_delay:
        resp = logged_in_client.post(
            reverse("downloader:submit"), {"urls": "https://open.spotify.com/track/abc123"}
        )
    batch = DownloadBatch.objects.get()
    assert batch.created_by == user
    assert resp.status_code == 302
    assert resp.url == reverse("downloader:batch_detail", args=[batch.id])
    mock_delay.assert_called_once_with(batch)


def test_submit_rejects_unsafe_url(logged_in_client):
    resp = logged_in_client.post(
        reverse("downloader:submit"), {"urls": "http://169.254.169.254/latest/meta-data/"}
    )
    assert resp.status_code == 302
    assert DownloadBatch.objects.count() == 0


def test_batch_detail_forbidden_for_other_user(logged_in_client, other_user):
    batch = DownloadBatch.objects.create(created_by=other_user, source_urls=["https://open.spotify.com/track/a"])
    resp = logged_in_client.get(reverse("downloader:batch_detail", args=[batch.id]))
    assert resp.status_code == 403


def test_batch_detail_visible_to_owner(logged_in_client, user):
    batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
    resp = logged_in_client.get(reverse("downloader:batch_detail", args=[batch.id]))
    assert resp.status_code == 200


class TestBatchRetryButtonsVisibility:
    # batch.failed_items/cancelled_items are plain counter columns, only
    # ever updated by recompute_status() (normally called by the task/view
    # that actually changes an item's status) — creating an item directly
    # via the ORM, as these tests do, doesn't touch them, so each test
    # calls it explicitly to reflect what the real code path would do.

    def test_hidden_when_nothing_failed_or_cancelled(self, logged_in_client, user):
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="a", status=ItemStatus.PENDING)
        batch.recompute_status()
        resp = logged_in_client.get(reverse("downloader:batch_detail", args=[batch.id]))
        assert b"Retry all" not in resp.content
        assert b"Retry failed" not in resp.content

    def test_both_shown_when_a_failed_item_exists(self, logged_in_client, user):
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="a", status=ItemStatus.FAILED)
        batch.recompute_status()
        resp = logged_in_client.get(reverse("downloader:batch_detail", args=[batch.id]))
        assert b"Retry all" in resp.content
        assert b"Retry failed" in resp.content

    def test_only_retry_all_shown_for_a_cancelled_only_batch(self, logged_in_client, user):
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="a", status=ItemStatus.CANCELLED)
        batch.recompute_status()
        resp = logged_in_client.get(reverse("downloader:batch_detail", args=[batch.id]))
        assert b"Retry all" in resp.content
        assert b"Retry failed" not in resp.content


class TestCLIOutputRemoved:
    """The staff-only CLI command/output panel was removed from the batch
    page entirely (it caused several rounds of polling/scroll bugs and
    was ultimately more noise than it was worth) — this only checks it's
    gone for everyone, including staff. `last_command`/`last_output` are
    still recorded on DownloadItem and stay visible in the Django admin
    (apps/downloader/admin.py) for anyone who genuinely needs to dig in.
    """

    def test_not_shown_even_to_staff(self, user):
        user.is_staff = True
        user.save()
        c = Client()
        c.force_login(user)
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(
            batch=batch, source_url="https://open.spotify.com/track/a",
            status=ItemStatus.FAILED, last_command="spotdl download ...", last_output="some output",
        )
        resp = c.get(reverse("downloader:batch_detail", args=[batch.id]))
        assert b"CLI output" not in resp.content
        assert b"spotdl download ..." not in resp.content
        assert b"some output" not in resp.content

    def test_error_message_still_shown_in_red(self, logged_in_client, user):
        # This is the part of the old panel worth keeping: a failed
        # item's error stays visible to its owner (not staff-only),
        # styled in red (Bootstrap's text-danger) — see
        # _item_status_badges.html.
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(
            batch=batch, source_url="https://open.spotify.com/track/a",
            status=ItemStatus.FAILED, error_message="HTTP Error 403: Forbidden",
        )
        resp = logged_in_client.get(reverse("downloader:batch_detail", args=[batch.id]))
        assert b'<div class="text-danger small">HTTP Error 403: Forbidden</div>' in resp.content


class TestHtmxBoostScoping:
    """Regression guard for base.html's hx-boost/#app-shell setup.

    <body> sets hx-select/hx-target="#app-shell" so boosted in-app
    navigation swaps only the app shell, leaving the persistent player
    outside it untouched. That attribute is inherited by *every*
    descendant htmx request unless overridden — including #batch-items'
    own 3s poll, whose response is a small fragment with no #app-shell
    in it at all. Without the override below, the inherited hx-select
    would find nothing on each poll and wipe the entire page down to the
    footer a few seconds after opening any batch (reported live: "kun
    menen esim batch 15 sivulle ja odotan hetken, kaikki tieto katoaa
    paitsi footer").
    """

    def test_poll_disinherits_the_app_shell_scoping(self, user):
        c = Client()
        c.force_login(user)
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        resp = c.get(reverse("downloader:batch_detail", args=[batch.id]))
        assert b'hx-select="unset" hx-target="unset"' in resp.content

    def test_retry_and_cancel_forms_restore_the_app_shell_scoping(self, user):
        # Nested inside #batch-items, so they'd otherwise inherit the
        # "unset" override above too — these need their own explicit
        # values to still boost through #app-shell (and so keep the
        # player playing through a retry/cancel click) rather than
        # falling back to htmx's un-scoped default (replacing all of
        # <body>, recreating the <audio> element and interrupting
        # playback).
        c = Client()
        c.force_login(user)
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(
            batch=batch, source_url="https://open.spotify.com/track/a", status=ItemStatus.FAILED,
        )
        resp = c.get(reverse("downloader:batch_detail", args=[batch.id]))
        assert b'hx-target="#app-shell" hx-select="#app-shell"' in resp.content


def test_item_retry_forbidden_for_non_owner(logged_in_client, other_user):
    batch = DownloadBatch.objects.create(created_by=other_user, source_urls=["https://open.spotify.com/track/a"])
    item = DownloadItem.objects.create(
        batch=batch, source_url="https://open.spotify.com/track/a", status=ItemStatus.FAILED,
    )
    resp = logged_in_client.post(reverse("downloader:item_retry", args=[item.id]))
    assert resp.status_code == 403
    item.refresh_from_db()
    assert item.status == ItemStatus.FAILED


def test_item_retry_requeues_failed_item(logged_in_client, user):
    batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
    item = DownloadItem.objects.create(
        batch=batch, source_url="https://open.spotify.com/track/a", status=ItemStatus.FAILED, retry_count=3,
    )
    with patch("apps.downloader.views.download_item_task.delay") as mock_delay:
        resp = logged_in_client.post(reverse("downloader:item_retry", args=[item.id]))
    assert resp.status_code == 302
    item.refresh_from_db()
    assert item.status == ItemStatus.PENDING
    assert item.retry_count == 0
    mock_delay.assert_called_once_with(item.id)


def test_batch_cancel_marks_pending_items_cancelled(logged_in_client, user):
    batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
    item = DownloadItem.objects.create(
        batch=batch, source_url="https://open.spotify.com/track/a", status=ItemStatus.PENDING,
    )
    resp = logged_in_client.post(reverse("downloader:batch_cancel", args=[batch.id]))
    assert resp.status_code == 302
    item.refresh_from_db()
    assert item.status == ItemStatus.CANCELLED


class TestBatchRetryFailed:
    def test_forbidden_for_non_owner(self, logged_in_client, other_user):
        batch = DownloadBatch.objects.create(created_by=other_user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="https://open.spotify.com/track/a", status=ItemStatus.FAILED)
        resp = logged_in_client.post(reverse("downloader:batch_retry_failed", args=[batch.id]))
        assert resp.status_code == 403

    def test_requeues_only_failed_items(self, logged_in_client, user):
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        failed = DownloadItem.objects.create(
            batch=batch, source_url="a", status=ItemStatus.FAILED, retry_count=3, error_message="boom",
        )
        cancelled = DownloadItem.objects.create(batch=batch, source_url="b", status=ItemStatus.CANCELLED)
        completed = DownloadItem.objects.create(batch=batch, source_url="c", status=ItemStatus.COMPLETED)

        with patch("apps.downloader.views.download_item_task.delay") as mock_delay:
            resp = logged_in_client.post(reverse("downloader:batch_retry_failed", args=[batch.id]))

        assert resp.status_code == 302
        failed.refresh_from_db()
        cancelled.refresh_from_db()
        completed.refresh_from_db()
        assert failed.status == ItemStatus.PENDING
        assert failed.retry_count == 0
        assert failed.error_message == ""
        assert cancelled.status == ItemStatus.CANCELLED  # untouched — not "failed"
        assert completed.status == ItemStatus.COMPLETED  # untouched
        mock_delay.assert_called_once_with(failed.id)

    def test_no_failed_items_queues_nothing(self, logged_in_client, user):
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="a", status=ItemStatus.COMPLETED)
        with patch("apps.downloader.views.download_item_task.delay") as mock_delay:
            resp = logged_in_client.post(reverse("downloader:batch_retry_failed", args=[batch.id]))
        assert resp.status_code == 302
        mock_delay.assert_not_called()


class TestBatchRetryAll:
    def test_forbidden_for_non_owner(self, logged_in_client, other_user):
        batch = DownloadBatch.objects.create(created_by=other_user, source_urls=["https://open.spotify.com/track/a"])
        resp = logged_in_client.post(reverse("downloader:batch_retry_all", args=[batch.id]))
        assert resp.status_code == 403

    def test_requeues_both_failed_and_cancelled_items(self, logged_in_client, user):
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        failed = DownloadItem.objects.create(batch=batch, source_url="a", status=ItemStatus.FAILED, retry_count=2)
        cancelled = DownloadItem.objects.create(
            batch=batch, source_url="b", status=ItemStatus.CANCELLED, cancelled_at=timezone.now(),
        )
        completed = DownloadItem.objects.create(batch=batch, source_url="c", status=ItemStatus.COMPLETED)

        with patch("apps.downloader.views.download_item_task.delay") as mock_delay:
            resp = logged_in_client.post(reverse("downloader:batch_retry_all", args=[batch.id]))

        assert resp.status_code == 302
        failed.refresh_from_db()
        cancelled.refresh_from_db()
        completed.refresh_from_db()
        assert failed.status == ItemStatus.PENDING
        assert cancelled.status == ItemStatus.PENDING
        assert cancelled.cancelled_at is None  # cleared — it's not cancelled anymore
        assert completed.status == ItemStatus.COMPLETED  # never re-downloaded
        assert mock_delay.call_count == 2

    def test_batch_status_recomputed_after_requeue(self, logged_in_client, user):
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="a", status=ItemStatus.FAILED)
        with patch("apps.downloader.views.download_item_task.delay"):
            logged_in_client.post(reverse("downloader:batch_retry_all", args=[batch.id]))
        batch.refresh_from_db()
        assert batch.status == BatchStatus.RUNNING

    def test_nothing_to_retry_queues_nothing(self, logged_in_client, user):
        batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="a", status=ItemStatus.COMPLETED)
        with patch("apps.downloader.views.download_item_task.delay") as mock_delay:
            resp = logged_in_client.post(reverse("downloader:batch_retry_all", args=[batch.id]))
        assert resp.status_code == 302
        mock_delay.assert_not_called()

