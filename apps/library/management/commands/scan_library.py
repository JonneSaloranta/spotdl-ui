from django.core.management.base import BaseCommand

from apps.library.services import scan_music_library


class Command(BaseCommand):
    help = (
        "Scan MUSIC_ROOT for audio files not yet in the library database and add them "
        "(CLAUDE.md #6/#25). Runs synchronously in this process — see `queue_library_scan` "
        "to run it on Celery instead."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--full", action="store_true",
            help="Full sync: also soft-delete rows for files no longer on disk, not just add new ones.",
        )

    def handle(self, *args, **options):
        report = scan_music_library(full=options["full"])
        self.stdout.write(self.style.SUCCESS(
            f"Files found: {report.files_found}\n"
            f"Tracks added: {report.tracks_added}\n"
            f"Tracks removed (full sync only): {report.tracks_removed}\n"
            f"Errors: {report.errors}"
        ))
