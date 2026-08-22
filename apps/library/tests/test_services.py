import errno
import os
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.core.models import SiteSettings
from apps.downloader.models import (
    DownloadBatch,
    DownloadItem,
    DuplicateStatus,
    ItemStatus,
)
from apps.library.models import TrackFile
from apps.library.services import (
    _atomic_move,
    finalize_download,
    find_exact_duplicate_groups,
    find_existing_download,
    remove_exact_duplicates,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    # SiteSettings.load() caches the singleton row (60s timeout) — a test
    # DB transaction rolling back doesn't clear that cache, so a test
    # that creates a SiteSettings row with non-default values can leak
    # into the next test otherwise (reproduced for real: a test setting
    # auto_replace_probable_duplicates=False made the *next* test's
    # SiteSettings.load() return that same stale, already-rolled-back
    # instance). Matches the same fixture already used in
    # apps/core/tests/test_settings_view.py.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def item():
    batch = DownloadBatch.objects.create(source_urls=["https://open.spotify.com/track/x"])
    return DownloadItem.objects.create(
        batch=batch,
        source_url="https://open.spotify.com/track/x",
        title="My Song",
        artist="My Artist",
        album="My Album",
        album_artist="My Artist",
    )


@pytest.fixture
def music_root(tmp_path, settings):
    root = tmp_path / "music"
    root.mkdir()
    settings.MUSIC_ROOT = root
    return root


def make_temp_audio(tmp_path, name="download.mp3", content=b"fake audio bytes"):
    path = tmp_path / name
    path.write_bytes(content)
    return path


class TestFinalizeDownloadNewTrack:
    def test_new_track_is_stored_and_hashed(self, item, music_root, tmp_path):
        temp_file = make_temp_audio(tmp_path)
        result = finalize_download(temp_file, item=item)

        assert result.duplicate_status == DuplicateStatus.NEW
        assert result.is_duplicate_skip is False
        assert TrackFile.objects.count() == 1
        track_file = TrackFile.objects.get()
        assert (music_root / track_file.path).exists()
        assert not temp_file.exists()  # moved, not copied

    def test_final_path_is_relative_and_flat_no_artist_album_subfolders(self, item, music_root, tmp_path):
        # Requested directly: every file lands in MUSIC_ROOT itself, not nested
        # under artist/album subfolders (unlike CLAUDE.md #22's suggested default).
        temp_file = make_temp_audio(tmp_path)
        result = finalize_download(temp_file, item=item)
        assert "/" not in result.track_file.path


class TestFinalizeDownloadExactDuplicate:
    def test_skip_policy_deletes_new_file_and_links_existing(self, item, music_root, tmp_path):
        SiteSettings.objects.create(pk=1, default_duplicate_policy="skip")

        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"identical content")
        first_result = finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"identical content")
        second_result = finalize_download(second_temp, item=second_item)

        assert second_result.duplicate_status == DuplicateStatus.EXACT_DUPLICATE
        assert second_result.is_duplicate_skip is True
        assert second_result.track_file.pk == first_result.track_file.pk
        assert not second_temp.exists()
        assert TrackFile.objects.count() == 1

    def test_keep_both_policy_stores_both_files(self, item, music_root, tmp_path):
        SiteSettings.objects.create(pk=1, default_duplicate_policy="keep_both")

        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"identical content")
        finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"identical content")
        second_result = finalize_download(second_temp, item=second_item)

        assert second_result.duplicate_status == DuplicateStatus.EXACT_DUPLICATE
        assert second_result.is_duplicate_skip is False
        assert TrackFile.objects.count() == 2


class TestFinalizeDownloadProbableDuplicate:
    def test_same_normalized_metadata_different_content_is_flagged_but_kept(self, item, music_root, tmp_path):
        # auto_replace_probable_duplicates defaults to True (see
        # TestAutoReplaceProbableDuplicate below for that behavior) — off
        # here so this test's own "always kept" premise holds regardless
        # of which file happens to be bigger.
        SiteSettings.objects.create(pk=1, auto_replace_probable_duplicates=False)
        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"content A")
        finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="MY SONG", artist="my artist", album="My Album", album_artist="My Artist",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"content B (different)")
        result = finalize_download(second_temp, item=second_item)

        assert result.duplicate_status == DuplicateStatus.PROBABLE_DUPLICATE
        assert result.is_duplicate_skip is False
        assert TrackFile.objects.count() == 2
        assert TrackFile.objects.filter(removed_at__isnull=True).count() == 2

    def test_never_overwrites_existing_file_on_name_collision(self, item, music_root, tmp_path):
        SiteSettings.objects.create(pk=1, auto_replace_probable_duplicates=False)
        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"content A")
        first_result = finalize_download(first_temp, item=item)
        original_bytes = (music_root / first_result.track_file.path).read_bytes()

        # Different content, but would sanitize to the exact same relative
        # path as the first file (same artist/album/title).
        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"totally different bytes")
        second_result = finalize_download(second_temp, item=second_item)

        assert (music_root / first_result.track_file.path).read_bytes() == original_bytes
        assert second_result.track_file.path != first_result.track_file.path


class TestAutoReplaceProbableDuplicate:
    """SiteSettings.auto_replace_probable_duplicates defaults to True —
    these all rely on that default, matching the actual out-of-the-box
    behavior (the tests above explicitly turn it off to test the
    always-kept-both mechanics in isolation)."""

    def test_bigger_new_file_replaces_the_older_one(self, item, music_root, tmp_path):
        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"short")
        first_result = finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"a much, much longer file body")
        second_result = finalize_download(second_temp, item=second_item)

        first_result.track_file.refresh_from_db()
        assert first_result.track_file.removed_at is not None
        assert not (music_root / first_result.track_file.path).exists()
        second_result.track_file.refresh_from_db()
        assert second_result.track_file.removed_at is None
        assert (music_root / second_result.track_file.path).exists()

    def test_smaller_new_file_does_not_replace_the_older_one(self, item, music_root, tmp_path):
        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"a much, much longer file body")
        first_result = finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"short")
        finalize_download(second_temp, item=second_item)

        first_result.track_file.refresh_from_db()
        assert first_result.track_file.removed_at is None
        assert (music_root / first_result.track_file.path).exists()

    def test_youtube_music_source_replaces_even_when_smaller(self, item, music_root, tmp_path):
        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"a much, much longer file body")
        first_result = finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
            source_provider="youtube-music",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"short")
        second_result = finalize_download(second_temp, item=second_item)

        first_result.track_file.refresh_from_db()
        assert first_result.track_file.removed_at is not None
        second_result.track_file.refresh_from_db()
        assert second_result.track_file.removed_at is None

    def test_plain_youtube_source_does_not_replace_on_its_own(self, item, music_root, tmp_path):
        # Deliberately excluded, unlike youtube-music: a plain YouTube
        # search is more likely to surface a live performance for the
        # same title/artist — genuinely different audio, not just a
        # worse copy — so source alone must not trigger a replace here.
        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"a much, much longer file body")
        first_result = finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
            source_provider="youtube",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"short")
        finalize_download(second_temp, item=second_item)

        first_result.track_file.refresh_from_db()
        assert first_result.track_file.removed_at is None

    def test_disabled_via_site_settings_never_replaces(self, item, music_root, tmp_path):
        SiteSettings.objects.create(pk=1, auto_replace_probable_duplicates=False)
        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"short")
        first_result = finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
            source_provider="youtube-music",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"a much, much longer file body")
        finalize_download(second_temp, item=second_item)

        first_result.track_file.refresh_from_db()
        assert first_result.track_file.removed_at is None

    def test_replacement_is_recorded_in_the_duplicate_match_audit_trail(self, item, music_root, tmp_path):
        from apps.library.models import DuplicateMatch

        first_temp = make_temp_audio(tmp_path, name="first.mp3", content=b"short")
        finalize_download(first_temp, item=item)

        second_item = DownloadItem.objects.create(
            batch=item.batch, source_url="https://open.spotify.com/track/y",
            title="My Song", artist="My Artist", album="My Album", album_artist="My Artist",
        )
        second_temp = make_temp_audio(tmp_path, name="second.mp3", content=b"a much, much longer file body")
        finalize_download(second_temp, item=second_item)

        match = DuplicateMatch.objects.get(match_type=DuplicateMatch.MatchType.METADATA)
        assert match.resolution == DuplicateMatch.Resolution.REPLACED


class TestAtomicMove:
    """Direct tests for the EXDEV (cross-filesystem) fallback.

    Regression coverage for the real production incident where MUSIC_ROOT and
    DOWNLOAD_TEMP_ROOT ended up as two separate Docker volumes: every
    finalize_download() call failed with OSError(EXDEV) from os.replace().
    """

    def test_same_filesystem_move_uses_a_plain_rename(self, tmp_path):
        src = tmp_path / "src.mp3"
        src.write_bytes(b"audio bytes")
        dest = tmp_path / "dest.mp3"

        with patch("apps.library.services.shutil.copyfileobj") as copyfileobj:
            _atomic_move(src, dest)

        copyfileobj.assert_not_called()  # plain os.replace(), no copy fallback needed
        assert dest.read_bytes() == b"audio bytes"
        assert not src.exists()

    def test_cross_filesystem_move_falls_back_to_copy_then_rename(self, tmp_path):
        src = tmp_path / "src.mp3"
        src.write_bytes(b"audio bytes")
        dest = tmp_path / "dest.mp3"

        real_replace = os.replace
        calls = []

        def fake_replace(a, b):
            calls.append((str(a), str(b)))
            if len(calls) == 1:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real_replace(a, b)

        with patch("apps.library.services.os.replace", side_effect=fake_replace):
            _atomic_move(src, dest)

        assert dest.read_bytes() == b"audio bytes"
        assert not src.exists()
        assert len(calls) == 2  # the failed direct rename, then the temp-file rename
        # No leftover temp file next to dest.
        assert list(tmp_path.iterdir()) == [dest]

    def test_other_os_errors_are_not_swallowed(self, tmp_path):
        src = tmp_path / "src.mp3"
        src.write_bytes(b"audio bytes")
        dest = tmp_path / "dest.mp3"

        with patch(
            "apps.library.services.os.replace",
            side_effect=OSError(errno.EACCES, "Permission denied"),
        ):
            with pytest.raises(OSError):
                _atomic_move(src, dest)

    def test_failure_mid_copy_leaves_no_partial_file_at_dest(self, tmp_path):
        src = tmp_path / "src.mp3"
        src.write_bytes(b"audio bytes")
        dest = tmp_path / "dest.mp3"

        def fake_replace(a, b):
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        with patch("apps.library.services.os.replace", side_effect=fake_replace):
            with patch(
                "apps.library.services.shutil.copyfileobj",
                side_effect=RuntimeError("disk full"),
            ):
                with pytest.raises(RuntimeError):
                    _atomic_move(src, dest)

        assert not dest.exists()  # never partially visible at the final path
        assert src.exists()  # original left alone; only the orphaned temp file is gone
        assert list(tmp_path.glob("*.part")) == []


def make_track_file(music_root, relative_path, content=b"audio bytes", **kwargs):
    """Create a TrackFile row with a matching real file under music_root,
    so remove_exact_duplicates()'s actual disk deletion has something
    real to act on."""
    path = music_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    kwargs.setdefault("filename", path.name)
    kwargs.setdefault("size", len(content))
    return TrackFile.objects.create(path=relative_path, **kwargs)


def backdate(track_file, **timedelta_kwargs):
    import datetime

    from django.utils import timezone

    TrackFile.objects.filter(pk=track_file.pk).update(
        created_at=timezone.now() - datetime.timedelta(**timedelta_kwargs)
    )
    track_file.refresh_from_db()
    return track_file


class TestFindExactDuplicateGroups:
    def test_no_duplicates_returns_no_groups(self, music_root):
        make_track_file(music_root, "a.mp3", content=b"aaa", sha256="a" * 64)
        make_track_file(music_root, "b.mp3", content=b"bbb", sha256="b" * 64)
        assert find_exact_duplicate_groups() == []

    def test_groups_files_sharing_a_hash(self, music_root):
        older = backdate(make_track_file(music_root, "a1.mp3", sha256="dup" * 21 + "d"), hours=2)
        newer = make_track_file(music_root, "a2.mp3", sha256="dup" * 21 + "d")

        groups = find_exact_duplicate_groups()

        assert len(groups) == 1
        assert [t.pk for t in groups[0]] == [older.pk, newer.pk]  # oldest first

    def test_ignores_probable_duplicates_with_different_hashes(self, music_root):
        make_track_file(music_root, "a.mp3", sha256="a" * 64, artist="X", title="Song")
        make_track_file(music_root, "b.mp3", sha256="b" * 64, artist="X", title="Song")
        assert find_exact_duplicate_groups() == []

    def test_ignores_soft_deleted_tracks(self, music_root):
        from django.utils import timezone

        make_track_file(music_root, "a.mp3", sha256="e" * 64, removed_at=timezone.now())
        make_track_file(music_root, "b.mp3", sha256="e" * 64)
        assert find_exact_duplicate_groups() == []


class TestRemoveExactDuplicates:
    def test_keeps_oldest_deletes_the_rest(self, music_root):
        older = backdate(make_track_file(music_root, "a1.mp3", sha256="dup" * 21 + "d"), hours=2)
        newer = make_track_file(music_root, "a2.mp3", sha256="dup" * 21 + "d")

        removed, reclaimed = remove_exact_duplicates()

        assert removed == 1
        assert reclaimed == newer.size
        older.refresh_from_db()
        newer.refresh_from_db()
        assert older.removed_at is None
        assert newer.removed_at is not None
        assert (music_root / "a1.mp3").exists()
        assert not (music_root / "a2.mp3").exists()

    def test_never_touches_the_kept_files_content(self, music_root):
        _older = backdate(make_track_file(music_root, "a1.mp3", content=b"original", sha256="f" * 64), hours=1)
        make_track_file(music_root, "a2.mp3", content=b"original", sha256="f" * 64)
        remove_exact_duplicates()
        assert (music_root / "a1.mp3").read_bytes() == b"original"

    def test_no_duplicates_removes_nothing(self, music_root):
        make_track_file(music_root, "a.mp3", sha256="a" * 64)
        removed, reclaimed = remove_exact_duplicates()
        assert (removed, reclaimed) == (0, 0)

    def test_missing_file_on_disk_leaves_the_row_alone(self, music_root):
        _older = backdate(make_track_file(music_root, "a1.mp3", sha256="g" * 64), hours=1)
        newer = make_track_file(music_root, "a2.mp3", sha256="g" * 64)
        (music_root / "a2.mp3").unlink()  # gone from disk before cleanup runs — Path.unlink(missing_ok=True)
        # still succeeds, so this exercises the "no error, but nothing to
        # actually reclaim from a file that's already gone" path, not the
        # OSError branch specifically.

        removed, _reclaimed = remove_exact_duplicates()

        assert removed == 1
        newer.refresh_from_db()
        assert newer.removed_at is not None

    def test_keeps_the_row_when_the_file_cannot_be_deleted(self, music_root):
        _older = backdate(make_track_file(music_root, "a1.mp3", sha256="h" * 64), hours=1)
        newer = make_track_file(music_root, "a2.mp3", sha256="h" * 64)

        with patch("apps.library.services.Path.unlink", side_effect=OSError("permission denied")):
            removed, reclaimed = remove_exact_duplicates()

        assert (removed, reclaimed) == (0, 0)
        newer.refresh_from_db()
        assert newer.removed_at is None

    def test_three_way_duplicate_keeps_only_the_oldest(self, music_root):
        oldest = backdate(make_track_file(music_root, "a1.mp3", sha256="i" * 64), hours=3)
        middle = backdate(make_track_file(music_root, "a2.mp3", sha256="i" * 64), hours=2)
        newest = make_track_file(music_root, "a3.mp3", sha256="i" * 64)

        removed, _reclaimed = remove_exact_duplicates()

        assert removed == 2
        oldest.refresh_from_db()
        middle.refresh_from_db()
        newest.refresh_from_db()
        assert oldest.removed_at is None
        assert middle.removed_at is not None
        assert newest.removed_at is not None


class TestFindExistingDownload:
    def make_completed_item(self, *, source_identifier="abc", source_url="https://open.spotify.com/track/abc",
                             track_file=None, status=ItemStatus.COMPLETED):
        batch = DownloadBatch.objects.create(source_urls=[source_url])
        track_file = track_file or TrackFile.objects.create(
            path="A/T.mp3", filename="T.mp3", size=5, sha256=source_identifier or source_url,
        )
        return DownloadItem.objects.create(
            batch=batch, source_url=source_url, source_identifier=source_identifier,
            title="T", artist="A", status=status, result_file=track_file, completed_at=timezone.now(),
        )

    def test_matches_by_source_identifier(self):
        item = self.make_completed_item(source_identifier="abc123")

        found = find_existing_download(source_identifier="abc123", source_url="https://different.url/whatever")

        assert found == item.result_file

    def test_matches_by_source_url_when_no_identifier(self):
        item = self.make_completed_item(source_identifier="", source_url="https://open.spotify.com/track/xyz")

        found = find_existing_download(source_identifier="", source_url="https://open.spotify.com/track/xyz")

        assert found == item.result_file

    def test_no_match_returns_none(self):
        self.make_completed_item(source_identifier="abc123")

        assert find_existing_download(source_identifier="does-not-exist", source_url="https://x/y") is None

    def test_blank_identifier_and_url_never_match_each_other(self):
        # Two items that both happen to have no identifier/URL must never be
        # treated as duplicates of each other purely because both are blank.
        self.make_completed_item(source_identifier="", source_url="")

        assert find_existing_download(source_identifier="", source_url="") is None

    def test_ignores_a_match_whose_file_was_since_removed(self):
        removed_file = TrackFile.objects.create(
            path="A/T.mp3", filename="T.mp3", size=5, sha256="removed", removed_at=timezone.now(),
        )
        self.make_completed_item(source_identifier="abc123", track_file=removed_file)

        assert find_existing_download(source_identifier="abc123", source_url="") is None

    def test_ignores_an_item_that_never_finished(self):
        batch = DownloadBatch.objects.create(source_urls=["https://open.spotify.com/track/abc"])
        DownloadItem.objects.create(
            batch=batch, source_url="https://open.spotify.com/track/abc", source_identifier="abc123",
            title="T", artist="A", status=ItemStatus.DOWNLOADING,
        )

        assert find_existing_download(source_identifier="abc123", source_url="") is None

    def test_duplicate_skipped_items_also_count_as_an_existing_download(self):
        item = self.make_completed_item(source_identifier="abc123", status=ItemStatus.DUPLICATE_SKIPPED)

        assert find_existing_download(source_identifier="abc123", source_url="") == item.result_file
