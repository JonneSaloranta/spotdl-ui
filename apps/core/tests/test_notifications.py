import pytest
from django.contrib.auth.models import User
from django.core import mail

from apps.downloader.models import BatchStatus, DownloadBatch, DownloadItem, ItemStatus

pytestmark = pytest.mark.django_db


def make_batch(user, **kwargs):
    return DownloadBatch.objects.create(
        created_by=user, source_urls=["https://open.spotify.com/track/x"], **kwargs
    )


def make_item(batch, status):
    return DownloadItem.objects.create(batch=batch, source_url="https://open.spotify.com/track/x", status=status)


class TestBatchFinishedEmail:
    def test_sends_email_when_batch_completes(self):
        user = User.objects.create_user(username="alice", password="x", email="alice@example.com")
        batch = make_batch(user)
        make_item(batch, ItemStatus.COMPLETED)

        batch.recompute_status()

        assert batch.status == BatchStatus.COMPLETED
        assert len(mail.outbox) == 1
        assert "alice@example.com" in mail.outbox[0].to

    def test_does_not_send_twice(self):
        user = User.objects.create_user(username="alice", password="x", email="alice@example.com")
        batch = make_batch(user)
        make_item(batch, ItemStatus.COMPLETED)

        batch.recompute_status()
        batch.recompute_status()  # e.g. a late redundant call

        assert len(mail.outbox) == 1

    def test_no_email_without_user_account(self):
        batch = make_batch(None)  # shared-link submission
        make_item(batch, ItemStatus.COMPLETED)
        batch.recompute_status()
        assert len(mail.outbox) == 0

    def test_no_email_when_user_has_no_address(self):
        user = User.objects.create_user(username="alice", password="x", email="")
        batch = make_batch(user)
        make_item(batch, ItemStatus.COMPLETED)
        batch.recompute_status()
        assert len(mail.outbox) == 0

    def test_respects_notify_on_batch_complete_preference(self):
        user = User.objects.create_user(username="alice", password="x", email="alice@example.com")
        user.profile.notify_on_batch_complete = False
        user.profile.save()
        batch = make_batch(user)
        make_item(batch, ItemStatus.COMPLETED)
        batch.recompute_status()
        assert len(mail.outbox) == 0

    def test_respects_notify_on_batch_failed_preference(self):
        user = User.objects.create_user(username="alice", password="x", email="alice@example.com")
        user.profile.notify_on_batch_failed = False
        user.profile.save()
        batch = make_batch(user)
        make_item(batch, ItemStatus.FAILED)
        batch.recompute_status()
        assert batch.status == BatchStatus.FAILED
        assert len(mail.outbox) == 0

    def test_failed_batch_still_notifies_by_default(self):
        user = User.objects.create_user(username="alice", password="x", email="alice@example.com")
        batch = make_batch(user)
        make_item(batch, ItemStatus.FAILED)
        batch.recompute_status()
        assert len(mail.outbox) == 1
        assert "attention" in mail.outbox[0].subject.lower()

    def test_no_email_while_batch_still_running(self):
        user = User.objects.create_user(username="alice", password="x", email="alice@example.com")
        batch = make_batch(user)
        make_item(batch, ItemStatus.DOWNLOADING)
        batch.recompute_status()
        assert batch.status == BatchStatus.RUNNING
        assert len(mail.outbox) == 0
