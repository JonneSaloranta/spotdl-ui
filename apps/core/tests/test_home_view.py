import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.downloader.models import DownloadBatch

pytestmark = pytest.mark.django_db


@pytest.fixture
def logged_in_client():
    user = User.objects.create_user(username="alice", password="x")
    c = Client()
    c.force_login(user)
    return c, user


class TestHomeRecentBatches:
    def test_shows_only_the_10_most_recent_batches(self, logged_in_client):
        client, user = logged_in_client
        batches = [
            DownloadBatch.objects.create(created_by=user, source_urls=[f"https://open.spotify.com/track/{i}"])
            for i in range(15)
        ]
        resp = client.get(reverse("core:home"))
        assert len(resp.context["batches"]) == 10
        # The 10 most recent, not just any 10 — newest-created first.
        shown_ids = {b.pk for b in resp.context["batches"]}
        assert shown_ids == {b.pk for b in batches[-10:]}

    def test_fewer_than_10_batches_all_shown(self, logged_in_client):
        client, user = logged_in_client
        DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/a"])
        DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/b"])
        resp = client.get(reverse("core:home"))
        assert len(resp.context["batches"]) == 2

    def test_only_shows_the_current_users_batches(self, logged_in_client):
        client, _user = logged_in_client
        other = User.objects.create_user(username="bob", password="x")
        DownloadBatch.objects.create(created_by=other, source_urls=["https://open.spotify.com/track/a"])
        resp = client.get(reverse("core:home"))
        assert list(resp.context["batches"]) == []
