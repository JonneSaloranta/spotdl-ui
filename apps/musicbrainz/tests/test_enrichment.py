from unittest.mock import MagicMock

import pytest

from apps.library.models import TrackFile
from apps.musicbrainz.models import MusicBrainzRecording
from apps.musicbrainz.services import (
    MusicBrainzError,
    RecordingCandidate,
    auto_enrich_track,
)

pytestmark = pytest.mark.django_db


def make_track_file(**kwargs):
    kwargs.setdefault("path", f"{kwargs.get('title', 'Track')}.mp3")
    kwargs.setdefault("filename", "Track.mp3")
    kwargs.setdefault("size", 100)
    kwargs.setdefault("sha256", "a" * 64)
    kwargs.setdefault("title", "Never Gonna Give You Up")
    kwargs.setdefault("artist", "Rick Astley")
    return TrackFile.objects.create(**kwargs)


def make_candidate(**kwargs):
    kwargs.setdefault("mbid", "mbid-1")
    kwargs.setdefault("title", "Never Gonna Give You Up")
    kwargs.setdefault("artist", "Rick Astley")
    kwargs.setdefault("artist_mbid", "artist-mbid-1")
    kwargs.setdefault("release", "Whenever You Need Somebody")
    kwargs.setdefault("release_mbid", "release-mbid-1")
    kwargs.setdefault("length_ms", 213000)
    kwargs.setdefault("score", 100)
    return RecordingCandidate(**kwargs)


def make_recording(**kwargs):
    kwargs.setdefault("mbid", "mbid-1")
    kwargs.setdefault("title", "Never Gonna Give You Up")
    kwargs.setdefault("artist", "Rick Astley")
    kwargs.setdefault("release", "Whenever You Need Somebody")
    return MusicBrainzRecording.objects.create(**kwargs)


class TestAutoEnrichTrack:
    def test_high_confidence_match_fills_in_musicbrainz_id(self):
        track_file = make_track_file(album="")
        recording = make_recording()
        client = MagicMock()
        client.search_recordings.return_value = [make_candidate(score=95)]
        client.get_recording.return_value = recording

        found = auto_enrich_track(track_file, client=client)

        track_file.refresh_from_db()
        assert found is True
        assert track_file.musicbrainz_id == "mbid-1"
        assert track_file.musicbrainz_checked_at is not None

    def test_fills_in_missing_album_but_not_populated_one(self):
        track_file = make_track_file(album="")
        recording = make_recording(release="Whenever You Need Somebody")
        client = MagicMock()
        client.search_recordings.return_value = [make_candidate(score=95)]
        client.get_recording.return_value = recording

        auto_enrich_track(track_file, client=client)

        track_file.refresh_from_db()
        assert track_file.album == "Whenever You Need Somebody"

    def test_never_overwrites_an_already_populated_album(self):
        track_file = make_track_file(album="My Own Album Title")
        recording = make_recording(release="A Different Release Title")
        client = MagicMock()
        client.search_recordings.return_value = [make_candidate(score=95)]
        client.get_recording.return_value = recording

        auto_enrich_track(track_file, client=client)

        track_file.refresh_from_db()
        assert track_file.album == "My Own Album Title"

    def test_never_overwrites_an_already_set_musicbrainz_id(self):
        track_file = make_track_file()
        track_file.musicbrainz_id = "already-set-mbid"
        track_file.save(update_fields=["musicbrainz_id"])
        recording = make_recording(mbid="a-different-mbid")
        client = MagicMock()
        client.search_recordings.return_value = [make_candidate(mbid="a-different-mbid", score=95)]
        client.get_recording.return_value = recording

        auto_enrich_track(track_file, client=client)

        track_file.refresh_from_db()
        assert track_file.musicbrainz_id == "already-set-mbid"

    def test_low_confidence_match_is_not_accepted_automatically(self):
        track_file = make_track_file()
        client = MagicMock()
        client.search_recordings.return_value = [make_candidate(score=50)]

        found = auto_enrich_track(track_file, client=client)

        track_file.refresh_from_db()
        assert found is False
        assert track_file.musicbrainz_id == ""
        client.get_recording.assert_not_called()

    def test_marks_checked_even_when_no_candidates_found(self):
        track_file = make_track_file()
        client = MagicMock()
        client.search_recordings.return_value = []

        found = auto_enrich_track(track_file, client=client)

        track_file.refresh_from_db()
        assert found is False
        assert track_file.musicbrainz_checked_at is not None

    def test_marks_checked_even_when_the_search_itself_fails(self):
        track_file = make_track_file()
        client = MagicMock()
        client.search_recordings.side_effect = MusicBrainzError("boom")

        found = auto_enrich_track(track_file, client=client)

        track_file.refresh_from_db()
        assert found is False
        assert track_file.musicbrainz_checked_at is not None

    def test_marks_checked_even_when_the_recording_fetch_fails(self):
        track_file = make_track_file()
        client = MagicMock()
        client.search_recordings.return_value = [make_candidate(score=95)]
        client.get_recording.side_effect = MusicBrainzError("boom")

        found = auto_enrich_track(track_file, client=client)

        track_file.refresh_from_db()
        assert found is False
        assert track_file.musicbrainz_checked_at is not None

    def test_skips_search_entirely_when_title_or_artist_is_blank(self):
        track_file = make_track_file(title="", artist="Rick Astley")
        client = MagicMock()

        auto_enrich_track(track_file, client=client)

        client.search_recordings.assert_not_called()

    def test_picks_the_highest_scoring_candidate(self):
        track_file = make_track_file()
        recording = make_recording(mbid="best-mbid")
        client = MagicMock()
        client.search_recordings.return_value = [
            make_candidate(mbid="worse-mbid", score=91),
            make_candidate(mbid="best-mbid", score=99),
        ]
        client.get_recording.return_value = recording

        auto_enrich_track(track_file, client=client)

        client.get_recording.assert_called_once_with("best-mbid")

    def test_links_the_recording_back_to_the_track_file(self):
        track_file = make_track_file()
        recording = make_recording()
        client = MagicMock()
        client.search_recordings.return_value = [make_candidate(score=95)]
        client.get_recording.return_value = recording

        auto_enrich_track(track_file, client=client)

        recording.refresh_from_db()
        assert recording.matched_track_file_id == track_file.id

    def test_does_not_overwrite_an_existing_match_on_the_recording(self):
        other_track = make_track_file(title="Other")
        track_file = make_track_file()
        recording = make_recording(matched_track_file=other_track)
        client = MagicMock()
        client.search_recordings.return_value = [make_candidate(score=95)]
        client.get_recording.return_value = recording

        auto_enrich_track(track_file, client=client)

        recording.refresh_from_db()
        assert recording.matched_track_file_id == other_track.id
