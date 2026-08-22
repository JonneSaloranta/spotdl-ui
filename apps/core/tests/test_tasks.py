from unittest.mock import patch

import pytest

from apps.core.cleanup import CleanupReport
from apps.core.tasks import run_cleanup_task

pytestmark = pytest.mark.django_db


def test_run_cleanup_task_returns_report_as_a_plain_dict():
    report = CleanupReport(
        abandoned_items=1, abandoned_batches=5, orphaned_temp_dirs=2,
        expired_shared_links=3,
    )
    with patch("apps.core.tasks.run_all_cleanup_tasks", return_value=report):
        result = run_cleanup_task()
    assert result == {
        "abandoned_items": 1, "abandoned_batches": 5, "orphaned_temp_dirs": 2,
        "expired_shared_links": 3,
    }
