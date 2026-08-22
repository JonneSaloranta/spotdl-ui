from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.library.models import TrackFile

pytestmark = pytest.mark.django_db


@pytest.fixture
def logged_in_client():
    user = User.objects.create_user(username="alice", password="x")
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def staff_client():
    user = User.objects.create_user(username="staffer", password="x", is_staff=True)
    c = Client()
    c.force_login(user)
    return c


def make_track(**kwargs):
    kwargs.setdefault("filename", "f.mp3")
    kwargs.setdefault("size", 1)
    return TrackFile.objects.create(**kwargs)


class TestTrackList:
    def test_requires_login(self, client: Client):
        resp = client.get(reverse("library:list"))
        assert resp.status_code == 302

    def test_lists_tracks(self, logged_in_client):
        make_track(path="a.mp3", sha256="a" * 64, title="Song A", artist="Artist A")
        resp = logged_in_client.get(reverse("library:list"))
        assert resp.status_code == 200
        assert b"Song A" in resp.content

    def test_orders_newest_first(self, logged_in_client):
        older = make_track(path="old.mp3", sha256="a" * 64, title="Old Song")
        newer = make_track(path="new.mp3", sha256="b" * 64, title="New Song")
        resp = logged_in_client.get(reverse("library:list"))
        tracks = list(resp.context["page"].object_list)
        assert [t.pk for t in tracks] == [newer.pk, older.pk]

    def test_search_filters_by_title_artist_album(self, logged_in_client):
        make_track(path="a.mp3", sha256="a" * 64, title="Alpha", artist="X", album="Y")
        make_track(path="b.mp3", sha256="b" * 64, title="Beta", artist="X", album="Y")
        resp = logged_in_client.get(reverse("library:list"), {"q": "Alpha"})
        assert b"Alpha" in resp.content
        assert b"Beta" not in resp.content

    def test_excludes_soft_deleted_tracks(self, logged_in_client):
        from django.utils import timezone

        make_track(path="a.mp3", sha256="a" * 64, title="Removed", removed_at=timezone.now())
        resp = logged_in_client.get(reverse("library:list"))
        assert b"Removed" not in resp.content

    def test_flags_exact_duplicates(self, logged_in_client):
        make_track(path="a.mp3", sha256="dup" * 21 + "d", title="Copy One")
        make_track(path="b.mp3", sha256="dup" * 21 + "d", title="Copy Two")
        make_track(path="c.mp3", sha256="e" * 64, title="Unique")
        resp = logged_in_client.get(reverse("library:list"))
        content = resp.content.decode()
        # Both duplicate rows should carry the badge; the unique one should
        # not. Each row that has it renders twice — once in the mobile
        # card layout, once in the md+ table (CLAUDE.md #13) — so two
        # duplicate tracks produce four occurrences, not two.
        assert content.count('text-bg-warning') == 4

    def test_pagination(self, logged_in_client):
        for i in range(60):
            make_track(path=f"{i}.mp3", sha256=f"{i:064d}", title=f"Track {i}")
        resp = logged_in_client.get(reverse("library:list"))
        assert resp.status_code == 200
        assert resp.context["page"].paginator.num_pages == 2


class TestTrackStream:
    def test_requires_login(self, client: Client):
        track = make_track(path="a.mp3", sha256="a" * 64)
        resp = client.get(reverse("library:stream", args=[track.pk]))
        assert resp.status_code == 302

    def test_404_for_soft_deleted_track(self, logged_in_client):
        from django.utils import timezone

        track = make_track(path="a.mp3", sha256="a" * 64, removed_at=timezone.now())
        resp = logged_in_client.get(reverse("library:stream", args=[track.pk]))
        assert resp.status_code == 404

    def test_404_for_nonexistent_track(self, logged_in_client):
        resp = logged_in_client.get(reverse("library:stream", args=[999999]))
        assert resp.status_code == 404

    def test_nginx_backend_sends_x_accel_redirect(self, logged_in_client, settings):
        settings.MUSIC_STREAMING_BACKEND = "nginx"
        track = make_track(path="Artist/Album/Song.mp3", sha256="a" * 64, mime_type="audio/mpeg")
        resp = logged_in_client.get(reverse("library:stream", args=[track.pk]))
        assert resp.status_code == 200
        assert resp["X-Accel-Redirect"] == "/protected-music/Artist/Album/Song.mp3"
        assert resp["Content-Type"] == "audio/mpeg"
        assert resp.content == b""  # nginx supplies the body, Django sends none

    def test_nginx_backend_url_encodes_the_path(self, logged_in_client, settings):
        settings.MUSIC_STREAMING_BACKEND = "nginx"
        track = make_track(path="Sam Fender/People Watching/Rein Me In (feat. X).mp3", sha256="a" * 64)
        resp = logged_in_client.get(reverse("library:stream", args=[track.pk]))
        assert resp["X-Accel-Redirect"] == (
            "/protected-music/Sam%20Fender/People%20Watching/Rein%20Me%20In%20%28feat.%20X%29.mp3"
        )

    def test_direct_backend_streams_the_file(self, logged_in_client, settings, tmp_path):
        settings.MUSIC_STREAMING_BACKEND = "direct"
        settings.MUSIC_ROOT = tmp_path
        (tmp_path / "song.mp3").write_bytes(b"fake audio bytes")
        track = make_track(path="song.mp3", sha256="a" * 64, mime_type="audio/mpeg")

        resp = logged_in_client.get(reverse("library:stream", args=[track.pk]))

        assert resp.status_code == 200
        assert resp["Content-Type"] == "audio/mpeg"
        assert b"".join(resp.streaming_content) == b"fake audio bytes"

    def test_direct_backend_404s_when_the_file_is_missing_on_disk(self, logged_in_client, settings, tmp_path):
        settings.MUSIC_STREAMING_BACKEND = "direct"
        settings.MUSIC_ROOT = tmp_path
        track = make_track(path="missing.mp3", sha256="a" * 64)

        resp = logged_in_client.get(reverse("library:stream", args=[track.pk]))

        assert resp.status_code == 404


class TestTrackCover:
    def test_requires_login(self, client: Client):
        track = make_track(path="a.mp3", sha256="a" * 64)
        resp = client.get(reverse("library:cover", args=[track.pk]))
        assert resp.status_code == 302

    def test_404_when_file_missing_on_disk(self, logged_in_client, settings, tmp_path):
        settings.MUSIC_ROOT = tmp_path
        track = make_track(path="missing.mp3", sha256="a" * 64)
        resp = logged_in_client.get(reverse("library:cover", args=[track.pk]))
        assert resp.status_code == 404

    def test_404_when_file_has_no_embedded_picture(self, logged_in_client, settings, tmp_path):
        settings.MUSIC_ROOT = tmp_path
        (tmp_path / "song.mp3").write_bytes(b"not really audio")
        track = make_track(path="song.mp3", sha256="a" * 64)
        resp = logged_in_client.get(reverse("library:cover", args=[track.pk]))
        assert resp.status_code == 404

    def test_serves_the_embedded_picture(self, logged_in_client, settings, tmp_path):
        settings.MUSIC_ROOT = tmp_path
        (tmp_path / "song.mp3").write_bytes(b"not really audio")
        track = make_track(path="song.mp3", sha256="a" * 64)

        with patch("apps.library.streaming.extract_cover_art", return_value=(b"fake jpeg bytes", "image/jpeg")):
            resp = logged_in_client.get(reverse("library:cover", args=[track.pk]))

        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/jpeg"
        assert resp.content == b"fake jpeg bytes"
        assert "private" in resp["Cache-Control"]

    def test_404_for_soft_deleted_track(self, logged_in_client):
        from django.utils import timezone

        track = make_track(path="a.mp3", sha256="a" * 64, removed_at=timezone.now())
        resp = logged_in_client.get(reverse("library:cover", args=[track.pk]))
        assert resp.status_code == 404


class TestTrackNeighbors:
    def test_requires_login(self, client: Client):
        track = make_track(path="a.mp3", sha256="a" * 64)
        resp = client.get(reverse("library:neighbors", args=[track.pk]))
        assert resp.status_code == 302

    def test_404_for_nonexistent_track(self, logged_in_client):
        resp = logged_in_client.get(reverse("library:neighbors", args=[999999]))
        assert resp.status_code == 404

    # Ordering is newest-first (most recently added track first — matches
    # the library page itself), so with tracks created in order a, b, c,
    # the display order is c, b, a: c is newest/first, a is oldest/last.

    def test_middle_track_has_both_neighbors(self, logged_in_client):
        a = make_track(path="a.mp3", sha256="a" * 64, artist="Artist", title="A")
        b = make_track(path="b.mp3", sha256="b" * 64, artist="Artist", title="B")
        c = make_track(path="c.mp3", sha256="c" * 64, artist="Artist", title="C")

        resp = logged_in_client.get(reverse("library:neighbors", args=[b.pk]))
        data = resp.json()

        assert data["prev"]["id"] == c.pk
        assert data["next"]["id"] == a.pk

    def test_newest_track_has_no_previous(self, logged_in_client):
        make_track(path="a.mp3", sha256="a" * 64, artist="Artist", title="A")
        b = make_track(path="b.mp3", sha256="b" * 64, artist="Artist", title="B")

        resp = logged_in_client.get(reverse("library:neighbors", args=[b.pk]))
        data = resp.json()

        assert data["prev"] is None
        assert data["next"]["id"] is not None

    def test_oldest_track_has_no_next(self, logged_in_client):
        a = make_track(path="a.mp3", sha256="a" * 64, artist="Artist", title="A")
        make_track(path="b.mp3", sha256="b" * 64, artist="Artist", title="B")

        resp = logged_in_client.get(reverse("library:neighbors", args=[a.pk]))
        data = resp.json()

        assert data["next"] is None
        assert data["prev"]["id"] is not None

    def test_neighbor_payload_shape(self, logged_in_client):
        a = make_track(path="a.mp3", sha256="a" * 64, artist="Artist", title="A", filename="a.mp3")
        b = make_track(path="b.mp3", sha256="b" * 64, artist="Artist", title="B", filename="b.mp3")

        resp = logged_in_client.get(reverse("library:neighbors", args=[a.pk]))
        data = resp.json()

        assert data["prev"] == {
            "id": b.pk,
            "url": reverse("library:stream", args=[b.pk]),
            "cover_url": reverse("library:cover", args=[b.pk]),
            "title": "B",
            "artist": "Artist",
        }

    def test_ignores_soft_deleted_tracks(self, logged_in_client):
        from django.utils import timezone

        a = make_track(path="a.mp3", sha256="a" * 64, artist="Artist", title="A")
        make_track(path="b.mp3", sha256="b" * 64, artist="Artist", title="B", removed_at=timezone.now())
        c = make_track(path="c.mp3", sha256="c" * 64, artist="Artist", title="C")

        resp = logged_in_client.get(reverse("library:neighbors", args=[a.pk]))
        data = resp.json()

        assert data["prev"]["id"] == c.pk


class TestRemoveDuplicatesView:
    def _make_dup_pair(self, music_root):
        import datetime

        from django.utils import timezone

        (music_root / "a1.mp3").write_bytes(b"aaa")
        (music_root / "a2.mp3").write_bytes(b"aaa")
        older = make_track(path="a1.mp3", sha256="d" * 64, size=3)
        newer = make_track(path="a2.mp3", sha256="d" * 64, size=3)
        TrackFile.objects.filter(pk=older.pk).update(
            created_at=timezone.now() - datetime.timedelta(hours=1)
        )
        return older, newer

    def test_requires_login(self, client: Client):
        resp = client.get(reverse("library:remove_duplicates"))
        assert resp.status_code == 302

    def test_forbidden_for_non_staff(self, logged_in_client):
        resp = logged_in_client.get(reverse("library:remove_duplicates"))
        assert resp.status_code == 403

    def test_get_previews_without_deleting_anything(self, staff_client, settings, tmp_path):
        settings.MUSIC_ROOT = tmp_path
        older, newer = self._make_dup_pair(tmp_path)

        resp = staff_client.get(reverse("library:remove_duplicates"))

        assert resp.status_code == 200
        assert resp.context["total_to_remove"] == 1
        older.refresh_from_db()
        newer.refresh_from_db()
        assert older.removed_at is None
        assert newer.removed_at is None
        assert (tmp_path / "a2.mp3").exists()

    def test_get_with_no_duplicates_shows_empty_state(self, staff_client, settings, tmp_path):
        settings.MUSIC_ROOT = tmp_path
        make_track(path="a.mp3", sha256="a" * 64)
        resp = staff_client.get(reverse("library:remove_duplicates"))
        assert resp.status_code == 200
        assert resp.context["groups"] == []

    def test_post_deletes_the_newer_copy_and_soft_deletes_its_row(self, staff_client, settings, tmp_path):
        settings.MUSIC_ROOT = tmp_path
        older, newer = self._make_dup_pair(tmp_path)

        resp = staff_client.post(reverse("library:remove_duplicates"))

        assert resp.status_code == 302
        older.refresh_from_db()
        newer.refresh_from_db()
        assert older.removed_at is None
        assert newer.removed_at is not None
        assert (tmp_path / "a1.mp3").exists()
        assert not (tmp_path / "a2.mp3").exists()

    def test_post_logs_an_audit_event(self, staff_client, settings, tmp_path):
        from apps.core.models import AuditLog

        settings.MUSIC_ROOT = tmp_path
        self._make_dup_pair(tmp_path)
        staff_client.post(reverse("library:remove_duplicates"))
        assert AuditLog.objects.filter(action="duplicates_removed").exists()

    def test_post_forbidden_for_non_staff(self, logged_in_client, settings, tmp_path):
        settings.MUSIC_ROOT = tmp_path
        _older, newer = self._make_dup_pair(tmp_path)
        resp = logged_in_client.post(reverse("library:remove_duplicates"))
        assert resp.status_code == 403
        newer.refresh_from_db()
        assert newer.removed_at is None
        assert (tmp_path / "a2.mp3").exists()


class TestLibraryScanViews:
    def test_quick_sync_requires_login(self, client: Client):
        resp = client.post(reverse("library:scan_quick"))
        assert resp.status_code == 302

    def test_quick_sync_requires_get_is_rejected(self, staff_client):
        resp = staff_client.get(reverse("library:scan_quick"))
        assert resp.status_code == 405

    def test_quick_sync_forbidden_for_non_staff(self, logged_in_client):
        with patch("apps.library.views.scan_library_task.delay") as mock_delay:
            resp = logged_in_client.post(reverse("library:scan_quick"))
        assert resp.status_code == 403
        mock_delay.assert_not_called()

    def test_quick_sync_queues_a_non_full_scan(self, staff_client):
        with patch("apps.library.views.scan_library_task.delay") as mock_delay:
            resp = staff_client.post(reverse("library:scan_quick"))
        assert resp.status_code == 302
        mock_delay.assert_called_once_with(full=False)

    def test_full_sync_queues_a_full_scan(self, staff_client):
        with patch("apps.library.views.scan_library_task.delay") as mock_delay:
            resp = staff_client.post(reverse("library:scan_full"))
        assert resp.status_code == 302
        mock_delay.assert_called_once_with(full=True)

    def test_full_sync_forbidden_for_non_staff(self, logged_in_client):
        with patch("apps.library.views.scan_library_task.delay") as mock_delay:
            resp = logged_in_client.post(reverse("library:scan_full"))
        assert resp.status_code == 403
        mock_delay.assert_not_called()

    def test_quick_sync_logs_an_audit_event(self, staff_client):
        from apps.core.models import AuditLog

        with patch("apps.library.views.scan_library_task.delay"):
            staff_client.post(reverse("library:scan_quick"))
        assert AuditLog.objects.filter(action="library_scan_triggered").exists()


class TestLibrarySyncNavbar:
    def test_shown_to_staff(self, staff_client):
        resp = staff_client.get(reverse("library:list"))
        assert reverse("library:scan_quick").encode() in resp.content
        assert reverse("library:scan_full").encode() in resp.content

    def test_hidden_from_non_staff(self, logged_in_client):
        resp = logged_in_client.get(reverse("library:list"))
        assert reverse("library:scan_quick").encode() not in resp.content
        assert reverse("library:scan_full").encode() not in resp.content
