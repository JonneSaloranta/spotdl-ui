from unittest.mock import MagicMock, patch

from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4, MP4Cover

from apps.library.coverart import extract_cover_art


class TestExtractCoverArt:
    def test_returns_none_for_missing_file(self, tmp_path):
        assert extract_cover_art(tmp_path / "missing.mp3") is None

    def test_returns_none_when_mutagen_cannot_read_the_file(self, tmp_path):
        path = tmp_path / "not-audio.mp3"
        path.write_bytes(b"definitely not an audio file")
        assert extract_cover_art(path) is None

    def test_extracts_id3_apic_frame(self, tmp_path):
        path = tmp_path / "song.mp3"
        path.write_bytes(b"placeholder")

        frame = MagicMock()
        frame.data = b"jpeg bytes"
        frame.mime = "image/jpeg"
        fake_id3 = MagicMock(spec=ID3)
        fake_id3.getall.return_value = [frame]
        fake_audio = MagicMock()
        fake_audio.tags = fake_id3

        with patch("mutagen.File", return_value=fake_audio):
            result = extract_cover_art(path)

        assert result == (b"jpeg bytes", "image/jpeg")
        fake_id3.getall.assert_called_once_with("APIC")

    def test_id3_with_no_apic_frame_returns_none(self, tmp_path):
        path = tmp_path / "song.mp3"
        path.write_bytes(b"placeholder")

        fake_id3 = MagicMock(spec=ID3)
        fake_id3.getall.return_value = []
        fake_audio = MagicMock()
        fake_audio.tags = fake_id3

        with patch("mutagen.File", return_value=fake_audio):
            assert extract_cover_art(path) is None

    def test_id3_frame_with_no_mime_falls_back_to_jpeg(self, tmp_path):
        path = tmp_path / "song.mp3"
        path.write_bytes(b"placeholder")

        frame = MagicMock()
        frame.data = b"bytes"
        frame.mime = ""
        fake_id3 = MagicMock(spec=ID3)
        fake_id3.getall.return_value = [frame]
        fake_audio = MagicMock()
        fake_audio.tags = fake_id3

        with patch("mutagen.File", return_value=fake_audio):
            result = extract_cover_art(path)

        assert result == (b"bytes", "image/jpeg")

    def test_extracts_flac_picture(self, tmp_path):
        path = tmp_path / "song.flac"
        path.write_bytes(b"placeholder")

        picture = MagicMock()
        picture.data = b"png bytes"
        picture.mime = "image/png"
        fake_audio = MagicMock(spec=FLAC)
        fake_audio.tags = None
        fake_audio.pictures = [picture]

        with patch("mutagen.File", return_value=fake_audio):
            result = extract_cover_art(path)

        assert result == (b"png bytes", "image/png")

    def test_flac_with_no_pictures_returns_none(self, tmp_path):
        path = tmp_path / "song.flac"
        path.write_bytes(b"placeholder")

        fake_audio = MagicMock(spec=FLAC)
        fake_audio.tags = None
        fake_audio.pictures = []

        with patch("mutagen.File", return_value=fake_audio):
            assert extract_cover_art(path) is None

    def test_extracts_mp4_cover(self, tmp_path):
        path = tmp_path / "song.m4a"
        path.write_bytes(b"placeholder")

        cover = MP4Cover(b"png bytes", imageformat=MP4Cover.FORMAT_PNG)
        fake_audio = MagicMock(spec=MP4)
        fake_audio.tags = {"covr": [cover]}

        with patch("mutagen.File", return_value=fake_audio):
            result = extract_cover_art(path)

        assert result == (b"png bytes", "image/png")

    def test_mp4_jpeg_format(self, tmp_path):
        path = tmp_path / "song.m4a"
        path.write_bytes(b"placeholder")

        cover = MP4Cover(b"jpeg bytes", imageformat=MP4Cover.FORMAT_JPEG)
        fake_audio = MagicMock(spec=MP4)
        fake_audio.tags = {"covr": [cover]}

        with patch("mutagen.File", return_value=fake_audio):
            result = extract_cover_art(path)

        assert result == (b"jpeg bytes", "image/jpeg")

    def test_mp4_with_no_covr_tag_returns_none(self, tmp_path):
        path = tmp_path / "song.m4a"
        path.write_bytes(b"placeholder")

        fake_audio = MagicMock(spec=MP4)
        fake_audio.tags = {}

        with patch("mutagen.File", return_value=fake_audio):
            assert extract_cover_art(path) is None

    def test_unhandled_format_returns_none(self, tmp_path):
        path = tmp_path / "song.ogg"
        path.write_bytes(b"placeholder")

        fake_audio = MagicMock()
        fake_audio.tags = None

        with patch("mutagen.File", return_value=fake_audio):
            assert extract_cover_art(path) is None

    def test_mutagen_returning_none_is_handled(self, tmp_path):
        path = tmp_path / "song.mp3"
        path.write_bytes(b"placeholder")

        with patch("mutagen.File", return_value=None):
            assert extract_cover_art(path) is None

    def test_exception_is_swallowed_and_returns_none(self, tmp_path):
        path = tmp_path / "song.mp3"
        path.write_bytes(b"placeholder")

        with patch("mutagen.File", side_effect=RuntimeError("boom")):
            assert extract_cover_art(path) is None
