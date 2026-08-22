from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


class TestScanLibraryCommand:
    def test_runs_quick_sync_by_default(self, tmp_path, settings):
        settings.MUSIC_ROOT = tmp_path
        (tmp_path / "Artist").mkdir()
        (tmp_path / "Artist" / "Song.mp3").write_bytes(b"audio")

        out = StringIO()
        call_command("scan_library", stdout=out)

        assert "Tracks added: 1" in out.getvalue()

    def test_full_flag_runs_full_sync(self, tmp_path, settings):
        settings.MUSIC_ROOT = tmp_path
        out = StringIO()
        with patch("apps.library.management.commands.scan_library.scan_music_library") as mock_scan:
            from apps.library.services import ScanReport
            mock_scan.return_value = ScanReport()
            call_command("scan_library", "--full", stdout=out)
        mock_scan.assert_called_once_with(full=True)


class TestQueueLibraryScanCommand:
    def test_queues_a_quick_sync_task(self):
        out = StringIO()
        with patch("apps.library.management.commands.queue_library_scan.scan_library_task.delay") as mock_delay:
            call_command("queue_library_scan", stdout=out)
        mock_delay.assert_called_once_with(full=False)
        assert "queued" in out.getvalue().lower()
