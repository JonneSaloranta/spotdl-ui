from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.downloader.models import DownloadBatch, DownloadItem, ItemStatus
from apps.library.models import TrackFile
from apps.sharing.models import SharedImportLink

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def admin_user():
    return User.objects.create_user(username="admin", password="x", is_staff=True)


@pytest.fixture
def link(admin_user):
    instance, raw_token = SharedImportLink.generate(
        created_by=admin_user, ttl_hours=24, maximum_uses=5, max_items_per_submission=2,
    )
    return instance, raw_token


class TestSharedSubmit:
    def test_invalid_token_returns_404(self, client: Client):
        resp = client.get(reverse("sharing:submit", args=["does-not-exist"]))
        assert resp.status_code == 404

    def test_valid_token_shows_form(self, client: Client, link):
        _instance, raw_token = link
        resp = client.get(reverse("sharing:submit", args=[raw_token]))
        assert resp.status_code == 200
        assert b"csrfmiddlewaretoken" in resp.content
        assert b'name="urls"' in resp.content

    def test_successful_submission_creates_batch_and_consumes_a_use(self, client: Client, link):
        instance, raw_token = link
        with patch("apps.sharing.views.queue_batch_resolve") as mock_delay:
            resp = client.post(
                reverse("sharing:submit", args=[raw_token]),
                {"urls": "https://open.spotify.com/track/abc123"},
            )
        assert resp.status_code == 200
        assert DownloadBatch.objects.filter(shared_link=instance).count() == 1
        instance.refresh_from_db()
        assert instance.uses == 1
        mock_delay.assert_called_once()

    def test_submission_never_associates_with_a_user_account(self, client: Client, link):
        instance, raw_token = link
        with patch("apps.sharing.views.queue_batch_resolve"):
            client.post(
                reverse("sharing:submit", args=[raw_token]),
                {"urls": "https://open.spotify.com/track/abc123"},
            )
        batch = DownloadBatch.objects.get(shared_link=instance)
        assert batch.created_by is None

    def test_too_many_urls_is_rejected(self, client: Client, link):
        _instance, raw_token = link
        resp = client.post(
            reverse("sharing:submit", args=[raw_token]),
            {"urls": "\n".join([
                "https://open.spotify.com/track/a",
                "https://open.spotify.com/track/b",
                "https://open.spotify.com/track/c",
            ])},
        )
        assert resp.status_code == 200
        assert DownloadBatch.objects.count() == 0

    def test_exhausted_link_still_renders_but_hides_the_form(self, client: Client, admin_user):
        _instance, raw_token = SharedImportLink.generate(
            created_by=admin_user, ttl_hours=24, maximum_uses=1, max_items_per_submission=5,
        )
        with patch("apps.sharing.views.queue_batch_resolve"):
            client.post(reverse("sharing:submit", args=[raw_token]), {"urls": "https://open.spotify.com/track/a"})

        resp = client.get(reverse("sharing:submit", args=[raw_token]))

        assert resp.status_code == 200  # not a 404 — the link is still viewable
        assert b'name="urls"' not in resp.content

    def test_exhausted_link_rejects_a_further_post(self, client: Client, admin_user):
        _instance, raw_token = SharedImportLink.generate(
            created_by=admin_user, ttl_hours=24, maximum_uses=1, max_items_per_submission=5,
        )
        with patch("apps.sharing.views.queue_batch_resolve"):
            client.post(reverse("sharing:submit", args=[raw_token]), {"urls": "https://open.spotify.com/track/a"})
            resp = client.post(
                reverse("sharing:submit", args=[raw_token]), {"urls": "https://open.spotify.com/track/b"},
            )

        assert resp.status_code == 200
        assert DownloadBatch.objects.count() == 1  # the second submission never went through

    def test_expired_link_is_unreachable(self, client: Client, admin_user):
        instance, raw_token = SharedImportLink.generate(
            created_by=admin_user, ttl_hours=24, maximum_uses=5, max_items_per_submission=5,
        )
        instance.expires_at = timezone.now() - timezone.timedelta(hours=1)
        instance.save()
        resp = client.get(reverse("sharing:submit", args=[raw_token]))
        assert resp.status_code == 404

    def test_disabled_link_is_unreachable(self, client: Client, link):
        instance, raw_token = link
        instance.enabled = False
        instance.save()
        resp = client.get(reverse("sharing:submit", args=[raw_token]))
        assert resp.status_code == 404

    def test_submit_rate_limit_blocks_excessive_requests(self, client: Client, admin_user):
        # A generous maximum_uses so the request count is bounded by the
        # rate limiter, not by the link's own usage limit.
        _instance, raw_token = SharedImportLink.generate(
            created_by=admin_user, ttl_hours=24, maximum_uses=100, max_items_per_submission=5,
        )
        with patch("apps.sharing.views.queue_batch_resolve"):
            for _ in range(5):
                client.post(reverse("sharing:submit", args=[raw_token]), {"urls": "https://open.spotify.com/track/a"})
            resp = client.post(reverse("sharing:submit", args=[raw_token]), {"urls": "https://open.spotify.com/track/a"})
        assert resp.status_code == 403


class TestSharedStatusAndDownloads:
    def test_shows_only_items_from_this_link(self, client: Client, admin_user):
        link_a, token_a = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        link_b, _token_b = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        batch_a = DownloadBatch.objects.create(shared_link=link_a, source_urls=["https://open.spotify.com/track/a"])
        batch_b = DownloadBatch.objects.create(shared_link=link_b, source_urls=["https://open.spotify.com/track/b"])
        DownloadItem.objects.create(batch=batch_a, source_url="x", title="Mine", artist="Me")
        DownloadItem.objects.create(batch=batch_b, source_url="y", title="Not mine", artist="Someone else")

        resp = client.get(reverse("sharing:submit", args=[token_a]))

        assert b"Mine" in resp.content
        assert b"Not mine" not in resp.content

    def test_never_shows_another_users_authenticated_batches(self, client: Client, admin_user):
        other_user = User.objects.create_user(username="other", password="x")
        DownloadBatch.objects.create(created_by=other_user, source_urls=["https://open.spotify.com/track/a"])
        _link_instance, token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)

        resp = client.get(reverse("sharing:submit", args=[token]))

        assert resp.context["items"].count() == 0

    def test_downloaded_music_list_shows_completed_tracks(self, client: Client, admin_user):
        link_instance, token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        batch = DownloadBatch.objects.create(shared_link=link_instance, source_urls=["https://open.spotify.com/track/a"])
        track = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="x", title="Finished Song")
        DownloadItem.objects.create(
            batch=batch, source_url="x", status=ItemStatus.COMPLETED, result_file=track, completed_at=timezone.now(),
        )

        resp = client.get(reverse("sharing:submit", args=[token]))

        assert b"Finished Song" in resp.content
        assert b"play-track-btn" in resp.content

    def test_downloaded_music_list_is_capped_at_20(self, client: Client, admin_user):
        link_instance, token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=50)
        batch = DownloadBatch.objects.create(shared_link=link_instance, source_urls=["https://open.spotify.com/track/a"])
        for i in range(25):
            track = TrackFile.objects.create(path=f"{i}.mp3", filename=f"{i}.mp3", size=5, sha256=f"{i:064d}")
            DownloadItem.objects.create(
                batch=batch, source_url=f"u{i}", status=ItemStatus.COMPLETED,
                result_file=track, completed_at=timezone.now(),
            )

        resp = client.get(reverse("sharing:submit", args=[token]))

        assert len(resp.context["tracks"]) == 20

    def test_status_partial_endpoint(self, client: Client, admin_user):
        link_instance, token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        batch = DownloadBatch.objects.create(shared_link=link_instance, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="x", title="Polled item")

        resp = client.get(reverse("sharing:status", args=[token]))

        assert resp.status_code == 200
        assert b"Polled item" in resp.content
        assert b'hx-select="unset" hx-target="unset"' in resp.content

    def test_status_partial_404s_for_invalid_token(self, client: Client):
        resp = client.get(reverse("sharing:status", args=["does-not-exist"]))
        assert resp.status_code == 404

    def test_exhausted_link_still_shows_status(self, client: Client, admin_user):
        link_instance, token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=1)
        link_instance.register_use()
        batch = DownloadBatch.objects.create(shared_link=link_instance, source_urls=["https://open.spotify.com/track/a"])
        DownloadItem.objects.create(batch=batch, source_url="x", title="Still visible")

        resp = client.get(reverse("sharing:submit", args=[token]))

        assert resp.status_code == 200
        assert b"Still visible" in resp.content


class TestSharedTrackStreamAndCover:
    def _make_completed_item(self, link_instance):
        batch = DownloadBatch.objects.create(shared_link=link_instance, source_urls=["https://open.spotify.com/track/a"])
        track = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="x")
        return DownloadItem.objects.create(
            batch=batch, source_url="x", status=ItemStatus.COMPLETED, result_file=track, completed_at=timezone.now(),
        )

    def test_stream_works_for_a_track_downloaded_through_the_link(self, client: Client, admin_user, settings, tmp_path):
        settings.MUSIC_STREAMING_BACKEND = "direct"
        settings.MUSIC_ROOT = tmp_path
        (tmp_path / "A").mkdir()
        (tmp_path / "A" / "T.mp3").write_bytes(b"audio bytes")
        link_instance, token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        item = self._make_completed_item(link_instance)

        resp = client.get(reverse("sharing:track_stream", args=[token, item.result_file_id]))

        assert resp.status_code == 200
        assert b"".join(resp.streaming_content) == b"audio bytes"

    def test_stream_404s_for_a_track_from_a_different_link(self, client: Client, admin_user):
        _link_a, token_a = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        link_b, _token_b = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        item_from_b = self._make_completed_item(link_b)

        resp = client.get(reverse("sharing:track_stream", args=[token_a, item_from_b.result_file_id]))

        assert resp.status_code == 404

    def test_stream_404s_for_an_invalid_token(self, client: Client, admin_user):
        link_instance, _token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        item = self._make_completed_item(link_instance)

        resp = client.get(reverse("sharing:track_stream", args=["does-not-exist", item.result_file_id]))

        assert resp.status_code == 404

    def test_stream_still_works_after_the_link_is_exhausted(self, client: Client, admin_user, settings, tmp_path):
        settings.MUSIC_STREAMING_BACKEND = "direct"
        settings.MUSIC_ROOT = tmp_path
        (tmp_path / "A").mkdir()
        (tmp_path / "A" / "T.mp3").write_bytes(b"audio bytes")
        link_instance, token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=1)
        item = self._make_completed_item(link_instance)
        link_instance.register_use()

        resp = client.get(reverse("sharing:track_stream", args=[token, item.result_file_id]))

        assert resp.status_code == 200

    def test_cover_404s_when_track_has_no_embedded_art(self, client: Client, admin_user, settings, tmp_path):
        settings.MUSIC_ROOT = tmp_path
        (tmp_path / "A").mkdir()
        (tmp_path / "A" / "T.mp3").write_bytes(b"not really audio")
        link_instance, token = SharedImportLink.generate(created_by=admin_user, ttl_hours=24, maximum_uses=5)
        item = self._make_completed_item(link_instance)

        resp = client.get(reverse("sharing:track_cover", args=[token, item.result_file_id]))

        assert resp.status_code == 404
