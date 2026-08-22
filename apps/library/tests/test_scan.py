from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.library.models import DuplicateMatch, TrackFile
from apps.library.services import scan_music_library

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def music_root(tmp_path, settings):
    root = tmp_path / "music"
    root.mkdir()
    settings.MUSIC_ROOT = root
    return root


def write_file(music_root, relative_path, content=b"fake audio bytes"):
    path = music_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestScanMusicLibraryQuickSync:
    def test_adds_a_row_for_a_file_not_yet_known(self, music_root):
        write_file(music_root, "Artist/Album/Song.mp3")

        report = scan_music_library()

        assert report.files_found == 1
        assert report.tracks_added == 1
        track = TrackFile.objects.get()
        assert track.path == "Artist/Album/Song.mp3"
        assert track.filename == "Song.mp3"
        assert track.size == len(b"fake audio bytes")

    def test_ignores_non_audio_files(self, music_root):
        write_file(music_root, "Artist/Album/cover.jpg")
        write_file(music_root, "Artist/Album/notes.txt")

        report = scan_music_library()

        assert report.files_found == 0
        assert TrackFile.objects.count() == 0

    def test_leaves_already_known_files_untouched(self, music_root):
        write_file(music_root, "Artist/Album/Song.mp3")
        existing = TrackFile.objects.create(
            path="Artist/Album/Song.mp3", filename="Song.mp3", size=1, sha256="a" * 64, title="Custom Title",
        )

        report = scan_music_library()

        assert report.tracks_added == 0
        existing.refresh_from_db()
        assert existing.title == "Custom Title"  # not re-imported/overwritten

    def test_missing_music_root_returns_empty_report(self, tmp_path, settings):
        settings.MUSIC_ROOT = tmp_path / "does-not-exist"
        report = scan_music_library()
        assert report.files_found == 0
        assert report.tracks_added == 0

    def test_does_not_remove_rows_for_missing_files(self, music_root):
        TrackFile.objects.create(path="Gone/Song.mp3", filename="Song.mp3", size=1, sha256="a" * 64)
        report = scan_music_library(full=False)
        assert report.tracks_removed == 0
        assert TrackFile.objects.get().removed_at is None

    def test_flags_exact_duplicate_but_keeps_both_files(self, music_root):
        write_file(music_root, "Existing/Song.mp3", content=b"same bytes")
        existing = TrackFile.objects.create(
            path="Existing/Song.mp3", filename="Song.mp3", size=1,
            sha256=__import__("hashlib").sha256(b"same bytes").hexdigest(),
        )
        write_file(music_root, "New/Song.mp3", content=b"same bytes")

        scan_music_library()

        # The scan never deletes a file a human placed there — only
        # exact-duplicate *removal* (a separate, explicit staff action:
        # remove_exact_duplicates()) or the download-pipeline's
        # auto-replace do that.
        existing.refresh_from_db()
        assert existing.removed_at is None
        assert TrackFile.objects.filter(removed_at__isnull=True).count() == 2
        assert DuplicateMatch.objects.filter(match_type=DuplicateMatch.MatchType.EXACT_HASH).exists()

    def test_flags_probable_duplicate(self, music_root):
        write_file(music_root, "Existing/Song.mp3")
        TrackFile.objects.create(
            path="Existing/Song.mp3", filename="Song.mp3", size=1, sha256="a" * 64,
            normalized_artist="an artist", normalized_title="a title",
        )
        write_file(music_root, "New/Song.mp3", content=b"different bytes entirely")

        with patch(
            "apps.library.services._extract_scan_tags",
            return_value={"title": "A Title", "artist": "An Artist", "album": "", "album_artist": ""},
        ):
            scan_music_library()

        assert DuplicateMatch.objects.filter(match_type=DuplicateMatch.MatchType.METADATA).exists()


class TestScanMusicLibraryFullSync:
    def test_soft_deletes_rows_for_files_no_longer_present(self, music_root):
        TrackFile.objects.create(path="Gone/Song.mp3", filename="Song.mp3", size=1, sha256="a" * 64)

        report = scan_music_library(full=True)

        assert report.tracks_removed == 1
        assert TrackFile.objects.get().removed_at is not None

    def test_leaves_already_removed_rows_alone(self, music_root):
        TrackFile.objects.create(
            path="Gone/Song.mp3", filename="Song.mp3", size=1, sha256="a" * 64, removed_at=timezone.now(),
        )
        report = scan_music_library(full=True)
        assert report.tracks_removed == 0

    def test_updates_last_scanned_at_for_present_files(self, music_root):
        write_file(music_root, "Artist/Album/Song.mp3")
        existing = TrackFile.objects.create(
            path="Artist/Album/Song.mp3", filename="Song.mp3", size=1, sha256="a" * 64,
        )
        assert existing.last_scanned_at is None

        scan_music_library(full=True)

        existing.refresh_from_db()
        assert existing.last_scanned_at is not None

    def test_still_adds_new_files_same_as_quick_sync(self, music_root):
        write_file(music_root, "Artist/Album/Song.mp3")
        report = scan_music_library(full=True)
        assert report.tracks_added == 1


class TestExtractScanTags:
    def test_reads_common_tags_via_mutagen_easy_mode(self, music_root):
        from apps.library.services import _extract_scan_tags

        path = write_file(music_root, "Artist/Album/Song.mp3")
        fake_audio = MagicMock()
        fake_audio.get.side_effect = lambda key: {
            "title": ["A Title"], "artist": ["An Artist"], "album": ["An Album"],
            "albumartist": ["Album Artist"], "tracknumber": ["3/12"], "discnumber": ["1"], "date": ["2024-05-01"],
        }.get(key)

        with patch("mutagen.File", return_value=fake_audio):
            tags = _extract_scan_tags(path)

        assert tags == {
            "title": "A Title", "artist": "An Artist", "album": "An Album", "album_artist": "Album Artist",
            "track_number": 3, "disc_number": 1, "year": 2024,
        }

    def test_missing_tags_degrade_gracefully(self, music_root):
        from apps.library.services import _extract_scan_tags

        path = write_file(music_root, "Artist/Album/Song.mp3")
        with patch("mutagen.File", return_value=None):
            assert _extract_scan_tags(path) == {}

    def test_unreadable_file_degrades_gracefully(self, music_root):
        from apps.library.services import _extract_scan_tags

        path = write_file(music_root, "Artist/Album/Song.mp3")
        with patch("mutagen.File", side_effect=RuntimeError("boom")):
            assert _extract_scan_tags(path) == {}
