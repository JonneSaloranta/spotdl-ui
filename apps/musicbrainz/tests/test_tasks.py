from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.core.models import SiteSettings
from apps.downloader.models import BatchStatus, DownloadBatch
from apps.library.models import TrackFile
from apps.musicbrainz import tasks

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    # SiteSettings.load() caches the singleton row (60s timeout) — without
    # clearing it, a test that changes musicbrainz_enabled/sweep_batch_size
    # can leak a stale cached instance into the next test (same recurring
    # pitfall as apps/core/tests/test_settings_view.py).
    cache.clear()
    yield
    cache.clear()


def make_track_file(**kwargs):
    kwargs.setdefault("path", "Track.mp3")
    kwargs.setdefault("filename", "Track.mp3")
    kwargs.setdefault("size", 100)
    kwargs.setdefault("sha256", "a" * 64)
    kwargs.setdefault("title", "T")
    kwargs.setdefault("artist", "A")
    return TrackFile.objects.create(**kwargs)


class TestEnrichTrackTask:
    def test_calls_auto_enrich_for_an_existing_active_track(self):
        track_file = make_track_file()
        with patch("apps.musicbrainz.tasks.auto_enrich_track") as mock_enrich:
            tasks.enrich_track_task(track_file.id)
        mock_enrich.assert_called_once_with(track_file)

    def test_does_nothing_when_musicbrainz_is_disabled(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"musicbrainz_enabled": False})
        track_file = make_track_file()
        with patch("apps.musicbrainz.tasks.auto_enrich_track") as mock_enrich:
            tasks.enrich_track_task(track_file.id)
        mock_enrich.assert_not_called()

    def test_does_nothing_for_a_removed_track(self):
        from django.utils import timezone
        track_file = make_track_file(removed_at=timezone.now())
        with patch("apps.musicbrainz.tasks.auto_enrich_track") as mock_enrich:
            tasks.enrich_track_task(track_file.id)
        mock_enrich.assert_not_called()

    def test_does_nothing_for_a_missing_track(self):
        with patch("apps.musicbrainz.tasks.auto_enrich_track") as mock_enrich:
            tasks.enrich_track_task(999999)
        mock_enrich.assert_not_called()


class TestMusicbrainzSweepTask:
    def test_skips_when_musicbrainz_disabled(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"musicbrainz_enabled": False})
        make_track_file()
        with patch("apps.musicbrainz.tasks.auto_enrich_track") as mock_enrich:
            result = tasks.musicbrainz_sweep_task()
        mock_enrich.assert_not_called()
        assert result["checked"] == 0

    def test_skips_while_a_batch_is_active(self):
        DownloadBatch.objects.create(source_urls=["https://x"], status=BatchStatus.RUNNING)
        make_track_file()
        with patch("apps.musicbrainz.tasks.auto_enrich_track") as mock_enrich:
            result = tasks.musicbrainz_sweep_task()
        mock_enrich.assert_not_called()
        assert result["skipped"] == "downloads_active"

    def test_runs_when_all_batches_are_finished(self):
        DownloadBatch.objects.create(source_urls=["https://x"], status=BatchStatus.COMPLETED)
        make_track_file()
        with patch("apps.musicbrainz.tasks.auto_enrich_track", return_value=True) as mock_enrich:
            result = tasks.musicbrainz_sweep_task()
        mock_enrich.assert_called_once()
        assert result == {"checked": 1, "matched": 1}

    def test_only_processes_unchecked_tracks(self):
        from django.utils import timezone
        make_track_file(sha256="a" * 64, musicbrainz_checked_at=timezone.now())
        unchecked = make_track_file(sha256="b" * 64, path="Other.mp3")
        with patch("apps.musicbrainz.tasks.auto_enrich_track", return_value=False) as mock_enrich:
            tasks.musicbrainz_sweep_task()
        mock_enrich.assert_called_once_with(unchecked)

    def test_ignores_removed_tracks(self):
        from django.utils import timezone
        make_track_file(removed_at=timezone.now())
        with patch("apps.musicbrainz.tasks.auto_enrich_track") as mock_enrich:
            tasks.musicbrainz_sweep_task()
        mock_enrich.assert_not_called()

    def test_respects_the_configured_batch_size(self):
        SiteSettings.objects.update_or_create(pk=1, defaults={"musicbrainz_sweep_batch_size": 2})
        for i in range(5):
            make_track_file(sha256=f"{i}" * 64, path=f"T{i}.mp3")
        with patch("apps.musicbrainz.tasks.auto_enrich_track", return_value=False) as mock_enrich:
            result = tasks.musicbrainz_sweep_task()
        assert mock_enrich.call_count == 2
        assert result["checked"] == 2
