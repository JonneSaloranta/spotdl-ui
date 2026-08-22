from unittest.mock import patch

import pytest

from apps.library.services import ScanReport
from apps.library.tasks import scan_library_task

pytestmark = pytest.mark.django_db


def test_scan_library_task_returns_report_as_a_plain_dict():
    report = ScanReport(files_found=5, tracks_added=2, tracks_removed=1, errors=1)
    with patch("apps.library.tasks.scan_music_library", return_value=report) as mock_scan:
        result = scan_library_task(full=True)
    mock_scan.assert_called_once_with(full=True)
    assert result == {"files_found": 5, "tracks_added": 2, "tracks_removed": 1, "errors": 1}


def test_scan_library_task_defaults_to_quick_sync():
    report = ScanReport()
    with patch("apps.library.tasks.scan_music_library", return_value=report) as mock_scan:
        scan_library_task()
    mock_scan.assert_called_once_with(full=False)
