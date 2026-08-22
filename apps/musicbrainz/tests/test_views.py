from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.library.models import TrackFile
from apps.musicbrainz.models import MusicBrainzRecording
from apps.musicbrainz.services import RecordingCandidate

pytestmark = pytest.mark.django_db


@pytest.fixture
def track_file():
    return TrackFile.objects.create(
        path="A/B.mp3", filename="B.mp3", size=1, sha256="a" * 64,
        title="Old Title", artist="Old Artist", album="Old Album",
    )


@pytest.fixture
def staff_client():
    user = User.objects.create_user(username="staff", password="x", is_staff=True)
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def normal_client():
    user = User.objects.create_user(username="user", password="x")
    c = Client()
    c.force_login(user)
    return c


class TestPermissions:
    def test_review_requires_staff(self, normal_client, track_file):
        resp = normal_client.get(reverse("musicbrainz:review", args=[track_file.pk]))
        assert resp.status_code in (302, 403)

    def test_review_accessible_to_staff(self, staff_client, track_file):
        resp = staff_client.get(reverse("musicbrainz:review", args=[track_file.pk]))
        assert resp.status_code == 200


class TestSearch:
    def test_search_renders_candidates(self, staff_client, track_file):
        candidate = RecordingCandidate(
            mbid="mbid-1", title="New Title", artist="New Artist", artist_mbid="",
            release="New Album", release_mbid="", length_ms=200000, score=100,
        )
        with patch("apps.musicbrainz.views.MusicBrainzClient.search_recordings", return_value=[candidate]):
            resp = staff_client.post(
                reverse("musicbrainz:review", args=[track_file.pk]),
                {"action": "search", "title": "New Title", "artist": "New Artist", "album": ""},
            )
        assert resp.status_code == 200
        assert b"New Title" in resp.content


class TestPreviewAndApply:
    def test_preview_shows_diff(self, staff_client, track_file):
        recording = MusicBrainzRecording.objects.create(
            mbid="mbid-1", title="New Title", artist="New Artist", release="New Album",
        )
        with patch("apps.musicbrainz.views.MusicBrainzClient.get_recording", return_value=recording):
            resp = staff_client.get(reverse("musicbrainz:preview", args=[track_file.pk, "mbid-1"]))
        assert resp.status_code == 200
        assert b"New Title" in resp.content
        assert b"Old Title" in resp.content

    def test_apply_updates_track_file(self, staff_client, track_file):
        recording = MusicBrainzRecording.objects.create(
            mbid="mbid-1", title="New Title", artist="New Artist", release="New Album",
        )
        with patch("apps.musicbrainz.views.MusicBrainzClient.get_recording", return_value=recording):
            resp = staff_client.post(reverse("musicbrainz:apply", args=[track_file.pk, "mbid-1"]))
        assert resp.status_code == 302
        track_file.refresh_from_db()
        assert track_file.title == "New Title"
        assert track_file.artist == "New Artist"
        assert track_file.album == "New Album"

    def test_apply_links_recording_to_track_file(self, staff_client, track_file):
        recording = MusicBrainzRecording.objects.create(
            mbid="mbid-1", title="New Title", artist="New Artist", release="New Album",
        )
        with patch("apps.musicbrainz.views.MusicBrainzClient.get_recording", return_value=recording):
            staff_client.post(reverse("musicbrainz:apply", args=[track_file.pk, "mbid-1"]))
        recording.refresh_from_db()
        assert recording.matched_track_file_id == track_file.id

    def test_apply_requires_post(self, staff_client, track_file):
        resp = staff_client.get(reverse("musicbrainz:apply", args=[track_file.pk, "mbid-1"]))
        assert resp.status_code == 302
