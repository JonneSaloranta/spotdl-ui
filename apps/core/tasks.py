import logging

from celery import shared_task

from apps.core.cleanup import run_all_cleanup_tasks

logger = logging.getLogger(__name__)


@shared_task
def run_cleanup_task() -> dict:
    """Scheduled cleanup entry point (CLAUDE.md #23). Safe to run
    concurrently with normal downloads — it only ever touches state that
    is unambiguously stale."""
    report = run_all_cleanup_tasks()
    logger.info("Cleanup finished: %s", report)
    return {
        "abandoned_items": report.abandoned_items,
        "abandoned_batches": report.abandoned_batches,
        "orphaned_temp_dirs": report.orphaned_temp_dirs,
        "expired_shared_links": report.expired_shared_links,
    }
