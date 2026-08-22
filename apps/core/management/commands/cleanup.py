from django.core.management.base import BaseCommand

from apps.core.cleanup import run_all_cleanup_tasks


class Command(BaseCommand):
    help = "Run scheduled cleanup: abandoned downloads, orphaned temp dirs, expired shared links (CLAUDE.md #23)."

    def handle(self, *args, **options):
        report = run_all_cleanup_tasks()
        self.stdout.write(self.style.SUCCESS(
            f"Abandoned items failed: {report.abandoned_items}\n"
            f"Abandoned batches failed: {report.abandoned_batches}\n"
            f"Orphaned temp directories removed: {report.orphaned_temp_dirs}\n"
            f"Expired shared links deleted: {report.expired_shared_links}"
        ))
