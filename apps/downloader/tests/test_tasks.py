from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.utils import timezone

from apps.core.models import SiteSettings
from apps.downloader import tasks
from apps.downloader.models import (
    BatchStatus,
    DownloadBatch,
    DownloadItem,
    DuplicateStatus,
    ItemStatus,
)
from apps.downloader.services import spotdl
from apps.library.models import TrackFile
from apps.library.services import FinalizeResult

pytestmark = pytest.mark.django_db


def make_batch(**kwargs):
    return DownloadBatch.objects.create(source_urls=["https://open.spotify.com/track/x"], **kwargs)


def make_item(batch, **kwargs):
    kwargs.setdefault("source_url", "https://open.spotify.com/track/x")
    kwargs.setdefault("title", "Some Title")
    kwargs.setdefault("artist", "Some Artist")
    return DownloadItem.objects.create(batch=batch, **kwargs)


def make_site_settings(**kwargs):
    kwargs.setdefault("primary_audio_provider", "youtube-music")
    kwargs.setdefault("primary_provider_attempts", 3)
    site_settings = SiteSettings.load()
    for key, value in kwargs.items():
        if key == "fallback_audio_providers_list":
            site_settings.set_fallback_audio_providers_list(value)
        else:
            setattr(site_settings, key, value)
    site_settings.save()
    return site_settings


class TestPickAudioProviders:
    def test_uses_primary_provider_for_early_attempts(self):
        site_settings = make_site_settings(fallback_audio_providers_list=["soundcloud"])

        # retry_count 0, 1, 2 -> attempts 1, 2, 3 (all still primary)
        assert tasks.pick_audio_providers(0, site_settings) == ["youtube-music"]
        assert tasks.pick_audio_providers(1, site_settings) == ["youtube-music"]
        assert tasks.pick_audio_providers(2, site_settings) == ["youtube-music"]

    def test_switches_to_fallback_once_primary_attempts_are_exhausted(self):
        site_settings = make_site_settings(fallback_audio_providers_list=["soundcloud"])

        # retry_count 3 -> attempt 4, past the 3 primary attempts
        assert tasks.pick_audio_providers(3, site_settings) == ["soundcloud"]
        assert tasks.pick_audio_providers(10, site_settings) == ["soundcloud"]

    def test_no_fallback_configured_keeps_using_primary(self):
        site_settings = make_site_settings(primary_provider_attempts=1, fallback_audio_providers_list=[])

        assert tasks.pick_audio_providers(5, site_settings) is None

    def test_empty_primary_provider_means_spotdls_own_default(self):
        site_settings = make_site_settings(primary_audio_provider="")

        assert tasks.pick_audio_providers(0, site_settings) is None

    def test_defaults_to_the_stored_site_settings_when_none_passed(self):
        make_site_settings(primary_audio_provider="soundcloud", primary_provider_attempts=5)
        assert tasks.pick_audio_providers(0) == ["soundcloud"]


class TestQueueBatchResolve:
    """queue_batch_resolve() must give resolve_batch_task a per-batch Celery
    time limit large enough to cover every one of the batch's source URLs
    individually timing out via SPOTDL_RESOLVE_TIMEOUT, not just one — see
    its docstring in apps/downloader/tasks.py for why the global
    CELERY_TASK_TIME_LIMIT alone isn't enough."""

    def test_sizes_time_limit_for_a_single_source(self, settings):
        settings.SPOTDL_RESOLVE_TIMEOUT = 500
        batch = DownloadBatch.objects.create(source_urls=["https://open.spotify.com/playlist/a"])

        with patch("apps.downloader.tasks.resolve_batch_task.apply_async") as mock_apply_async:
            tasks.queue_batch_resolve(batch)

        mock_apply_async.assert_called_once()
        _, kwargs = mock_apply_async.call_args
        assert kwargs["args"] == [batch.id]
        assert kwargs["time_limit"] == 560
        assert kwargs["soft_time_limit"] == 530

    def test_sizes_time_limit_proportionally_for_multiple_sources(self, settings):
        settings.SPOTDL_RESOLVE_TIMEOUT = 500
        batch = DownloadBatch.objects.create(source_urls=[
            "https://open.spotify.com/playlist/a",
            "https://open.spotify.com/playlist/b",
            "https://open.spotify.com/playlist/c",
        ])

        with patch("apps.downloader.tasks.resolve_batch_task.apply_async") as mock_apply_async:
            tasks.queue_batch_resolve(batch)

        _, kwargs = mock_apply_async.call_args
        assert kwargs["time_limit"] == 560 * 3


class TestResolveBatchTask:
    def test_resolves_and_queues_items(self):
        batch = make_batch()
        track = spotdl.ResolvedTrack(
            source_identifier="abc", source_url="https://open.spotify.com/track/abc",
            title="T", artist="A", album="Al", album_artist="Al",
            duration_seconds=100, track_number=1, disc_number=1, isrc=None,
        )
        with patch.object(spotdl, "resolve_source", return_value=[track]), \
             patch.object(tasks.download_item_task, "delay") as mock_delay:
            tasks.resolve_batch_task(batch.id)

        batch.refresh_from_db()
        assert batch.items.count() == 1
        mock_delay.assert_called_once()

    def test_idempotent_on_redelivery(self):
        batch = make_batch()
        track = spotdl.ResolvedTrack(
            source_identifier="abc", source_url="https://open.spotify.com/track/abc",
            title="T", artist="A", album="Al", album_artist="Al",
            duration_seconds=100, track_number=1, disc_number=1, isrc=None,
        )
        with patch.object(spotdl, "resolve_source", return_value=[track]), \
             patch.object(tasks.download_item_task, "delay"):
            tasks.resolve_batch_task(batch.id)

        # Simulate the batch moving on, then the task being redelivered
        # (e.g. a worker crash before the broker ack) — it must not
        # duplicate the already-created item nor re-queue a finished one.
        item = batch.items.get()
        item.status = ItemStatus.COMPLETED
        item.save()
        batch.status = BatchStatus.RUNNING
        batch.save()

        with patch.object(spotdl, "resolve_source", return_value=[track]), \
             patch.object(tasks.download_item_task, "delay") as mock_delay:
            tasks.resolve_batch_task(batch.id)

        assert batch.items.count() == 1
        mock_delay.assert_not_called()

    def test_caps_tracks_per_source(self, settings):
        settings.MAX_TRACKS_PER_SOURCE = 3
        batch = make_batch()
        tracks = [
            spotdl.ResolvedTrack(
                source_identifier=str(i), source_url=f"https://open.spotify.com/track/{i}",
                title=f"T{i}", artist="A", album="Al", album_artist="Al",
                duration_seconds=100, track_number=i, disc_number=1, isrc=None,
            )
            for i in range(10)
        ]
        with patch.object(spotdl, "resolve_source", return_value=tracks), \
             patch.object(tasks.download_item_task, "delay") as mock_delay:
            tasks.resolve_batch_task(batch.id)

        assert batch.items.count() == 3
        assert mock_delay.call_count == 3
        batch.refresh_from_db()
        assert "per-source limit" in batch.error_summary

    def test_skips_download_when_the_exact_source_was_already_downloaded(self):
        # Same track (same source_identifier/source_url), found via a different
        # playlist in a brand-new batch — must not spend a download attempt
        # re-confirming what a previous import already established.
        track_file = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="known")
        earlier_batch = make_batch()
        make_item(
            earlier_batch, source_url="https://open.spotify.com/track/abc", source_identifier="abc",
            status=ItemStatus.COMPLETED, result_file=track_file, completed_at=timezone.now(),
        )

        batch = make_batch()
        track = spotdl.ResolvedTrack(
            source_identifier="abc", source_url="https://open.spotify.com/track/abc",
            title="T", artist="A", album="Al", album_artist="Al",
            duration_seconds=100, track_number=1, disc_number=1, isrc=None,
        )
        with patch.object(spotdl, "resolve_source", return_value=[track]), \
             patch.object(tasks.download_item_task, "delay") as mock_delay:
            tasks.resolve_batch_task(batch.id)

        mock_delay.assert_not_called()
        item = batch.items.get()
        assert item.status == ItemStatus.DUPLICATE_SKIPPED
        assert item.result_file_id == track_file.id
        assert item.duplicate_status == DuplicateStatus.EXACT_DUPLICATE
        assert item.completed_at is not None

    def test_downloads_again_if_the_earlier_files_track_was_removed(self):
        track_file = TrackFile.objects.create(
            path="A/T.mp3", filename="T.mp3", size=5, sha256="known", removed_at=timezone.now(),
        )
        earlier_batch = make_batch()
        make_item(
            earlier_batch, source_url="https://open.spotify.com/track/abc", source_identifier="abc",
            status=ItemStatus.COMPLETED, result_file=track_file, completed_at=timezone.now(),
        )

        batch = make_batch()
        track = spotdl.ResolvedTrack(
            source_identifier="abc", source_url="https://open.spotify.com/track/abc",
            title="T", artist="A", album="Al", album_artist="Al",
            duration_seconds=100, track_number=1, disc_number=1, isrc=None,
        )
        with patch.object(spotdl, "resolve_source", return_value=[track]), \
             patch.object(tasks.download_item_task, "delay") as mock_delay:
            tasks.resolve_batch_task(batch.id)

        mock_delay.assert_called_once()
        item = batch.items.get()
        assert item.status == ItemStatus.PENDING

    def test_all_sources_failing_marks_batch_failed(self):
        batch = make_batch()
        with patch.object(spotdl, "resolve_source", side_effect=spotdl.SpotDLResolveError("boom")):
            tasks.resolve_batch_task(batch.id)
        batch.refresh_from_db()
        assert batch.status == BatchStatus.FAILED
        assert "boom" in batch.error_summary

    def test_does_not_reresolve_a_running_batch(self):
        batch = make_batch(status=BatchStatus.RUNNING)
        with patch.object(spotdl, "resolve_source") as mock_resolve:
            tasks.resolve_batch_task(batch.id)
        mock_resolve.assert_not_called()


class TestDownloadItemTask:
    @pytest.fixture(autouse=True)
    def _no_musicbrainz_dispatch(self):
        # A successful, non-duplicate-skip download queues enrich_track_task —
        # its own task, deliberately not exercised by these tests (see
        # apps/musicbrainz/tests/test_tasks.py for that), so keep it from
        # trying to talk to a real broker here.
        with patch.object(tasks.enrich_track_task, "delay") as mock_delay:
            yield mock_delay

    def test_completed_item_is_a_noop(self):
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.COMPLETED)
        with patch.object(spotdl, "download_track") as mock_download:
            tasks.download_item_task(item.id)
        mock_download.assert_not_called()

    def test_successful_download_finalizes_and_completes(self, tmp_path):
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING)
        fake_file = tmp_path / "track.mp3"
        fake_file.write_bytes(b"audio")

        outcome = spotdl.DownloadOutcome(
            success=True, exit_code=0, file_path=fake_file, stdout_tail="", stderr_tail="",
        )
        track_file = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="x")
        finalize_result = FinalizeResult(
            track_file=track_file, duplicate_status="new", is_duplicate_skip=False,
        )

        with patch.object(spotdl, "download_track", return_value=outcome), \
             patch("apps.downloader.tasks.finalize_download", return_value=finalize_result):
            tasks.download_item_task(item.id)

        item.refresh_from_db()
        assert item.status == ItemStatus.COMPLETED
        assert item.progress == 100

    def test_queues_musicbrainz_enrichment_for_a_new_non_duplicate_track(
        self, tmp_path, _no_musicbrainz_dispatch,
    ):
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING)
        fake_file = tmp_path / "track.mp3"
        fake_file.write_bytes(b"audio")
        outcome = spotdl.DownloadOutcome(
            success=True, exit_code=0, file_path=fake_file, stdout_tail="", stderr_tail="",
        )
        track_file = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="x")
        finalize_result = FinalizeResult(track_file=track_file, duplicate_status="new", is_duplicate_skip=False)

        with patch.object(spotdl, "download_track", return_value=outcome), \
             patch("apps.downloader.tasks.finalize_download", return_value=finalize_result):
            tasks.download_item_task(item.id)

        _no_musicbrainz_dispatch.assert_called_once_with(track_file.id)

    def test_does_not_queue_musicbrainz_enrichment_for_a_duplicate_skip(
        self, tmp_path, _no_musicbrainz_dispatch,
    ):
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING)
        fake_file = tmp_path / "track.mp3"
        fake_file.write_bytes(b"audio")
        outcome = spotdl.DownloadOutcome(
            success=True, exit_code=0, file_path=fake_file, stdout_tail="", stderr_tail="",
        )
        track_file = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="x")
        finalize_result = FinalizeResult(
            track_file=track_file, duplicate_status="exact_duplicate", is_duplicate_skip=True,
        )

        with patch.object(spotdl, "download_track", return_value=outcome), \
             patch("apps.downloader.tasks.finalize_download", return_value=finalize_result):
            tasks.download_item_task(item.id)

        _no_musicbrainz_dispatch.assert_not_called()

    def test_uses_the_tiered_provider_for_the_current_attempt(self, tmp_path):
        make_site_settings(fallback_audio_providers_list=["soundcloud"])

        batch = make_batch()
        # retry_count=3 -> this is attempt 4, past the primary tier.
        item = make_item(batch, status=ItemStatus.PENDING, retry_count=3)
        fake_file = tmp_path / "track.mp3"
        fake_file.write_bytes(b"audio")

        outcome = spotdl.DownloadOutcome(
            success=True, exit_code=0, file_path=fake_file, stdout_tail="", stderr_tail="",
        )
        track_file = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="fallback")
        finalize_result = FinalizeResult(track_file=track_file, duplicate_status="new", is_duplicate_skip=False)

        with patch.object(spotdl, "download_track", return_value=outcome) as mock_download, \
             patch("apps.downloader.tasks.finalize_download", return_value=finalize_result):
            tasks.download_item_task(item.id)

        assert mock_download.call_args.kwargs["audio_providers"] == ["soundcloud"]
        item.refresh_from_db()
        assert "soundcloud" in item.current_stage or "soundcloud" in item.last_command

    def test_on_progress_callback_updates_the_item_live(self, tmp_path):
        # download_track() is mocked (no real spotDL process here — that
        # mechanism is covered directly against a real subprocess in
        # test_spotdl_service.py), but the callback tasks.py builds and
        # passes to it is real: invoke it exactly as spotdl._run() would,
        # mid-call, and check it actually reached the database at that
        # point — not just that the task didn't raise.
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING)
        fake_file = tmp_path / "track.mp3"
        fake_file.write_bytes(b"audio")
        captured = {}

        def fake_download_track(*args, **kwargs):
            kwargs["on_progress"](35, "Getting audio meta")
            # Snapshot right here, before the task moves on to its own
            # later progress writes (80%, then 100%) — those would
            # otherwise overwrite the evidence that this callback ran.
            captured["progress"], captured["stage"] = DownloadItem.objects.values_list(
                "progress", "current_stage",
            ).get(pk=item.id)
            return spotdl.DownloadOutcome(
                success=True, exit_code=0, file_path=fake_file, stdout_tail="", stderr_tail="",
            )

        track_file = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="z")
        finalize_result = FinalizeResult(track_file=track_file, duplicate_status="new", is_duplicate_skip=False)

        with patch.object(spotdl, "download_track", side_effect=fake_download_track), \
             patch("apps.downloader.tasks.finalize_download", return_value=finalize_result):
            tasks.download_item_task(item.id)

        assert captured == {"progress": 35, "stage": "Getting audio meta"}
        item.refresh_from_db()
        assert item.status == ItemStatus.COMPLETED  # the task still completes normally afterward
        assert item.progress == 100

    def test_records_command_and_output_for_troubleshooting(self, tmp_path):
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING)
        fake_file = tmp_path / "track.mp3"
        fake_file.write_bytes(b"audio")

        outcome = spotdl.DownloadOutcome(
            success=True, exit_code=0, file_path=fake_file,
            stdout_tail="downloading...", stderr_tail="",
        )
        track_file = TrackFile.objects.create(path="A/T.mp3", filename="T.mp3", size=5, sha256="y")
        finalize_result = FinalizeResult(
            track_file=track_file, duplicate_status="new", is_duplicate_skip=False,
        )

        with patch.object(spotdl, "download_track", return_value=outcome), \
             patch("apps.downloader.tasks.finalize_download", return_value=finalize_result):
            tasks.download_item_task(item.id)

        item.refresh_from_db()
        assert "spotdl" in item.last_command
        assert item.source_url in item.last_command
        assert "downloading..." in item.last_output

    def test_records_command_even_when_download_raises(self):
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING)
        with patch.object(spotdl, "download_track", side_effect=spotdl.SpotDLTimeoutError("timed out")):
            with pytest.raises(Retry):
                tasks.download_item_task(item.id)
        item.refresh_from_db()
        assert "spotdl" in item.last_command

    def test_failed_download_retries_up_to_configured_limit(self, settings):
        settings.SPOTDL_MAX_RETRIES = 2
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING, retry_count=0)

        outcome = spotdl.DownloadOutcome(
            success=False, exit_code=1, file_path=None, stdout_tail="", stderr_tail="",
            error_message="no match found",
        )
        with patch.object(spotdl, "download_track", return_value=outcome):
            with pytest.raises(Retry):
                tasks.download_item_task(item.id)

        item.refresh_from_db()
        assert item.status == ItemStatus.PENDING
        assert item.retry_count == 1
        assert item.error_message == "no match found"

    def test_pending_retry_resets_progress_and_shows_retry_state(self, settings):
        # Regression guard: a mid-download progress value (e.g. 10%) must
        # not be left stale while an item sits waiting for its retry — it
        # must reset to 0 and the current_stage must say a retry is
        # scheduled, or a real retry-in-progress looks identical to a
        # stuck download in the UI.
        settings.SPOTDL_MAX_RETRIES = 3
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING, retry_count=0, progress=10)

        outcome = spotdl.DownloadOutcome(
            success=False, exit_code=1, file_path=None, stdout_tail="", stderr_tail="",
            error_message="HTTP Error 403: Forbidden",
        )
        with patch.object(spotdl, "download_track", return_value=outcome):
            with pytest.raises(Retry):
                tasks.download_item_task(item.id)

        item.refresh_from_db()
        assert item.progress == 0
        assert "retrying" in item.current_stage
        assert "1/3" in item.current_stage

    def test_failed_download_gives_up_after_max_retries(self, settings):
        settings.SPOTDL_MAX_RETRIES = 1
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING, retry_count=1)

        outcome = spotdl.DownloadOutcome(
            success=False, exit_code=1, file_path=None, stdout_tail="", stderr_tail="",
            error_message="still failing",
        )
        with patch.object(spotdl, "download_track", return_value=outcome):
            tasks.download_item_task(item.id)

        item.refresh_from_db()
        assert item.status == ItemStatus.FAILED
        batch.refresh_from_db()
        assert batch.status == BatchStatus.FAILED

    def test_cancellation_marks_item_cancelled(self):
        batch = make_batch()
        item = make_item(batch, status=ItemStatus.PENDING)
        with patch.object(spotdl, "download_track", side_effect=spotdl.SpotDLCancelledError("stop")):
            tasks.download_item_task(item.id)
        item.refresh_from_db()
        assert item.status == ItemStatus.CANCELLED
        assert item.cancelled_at is not None
