from django.core.management.base import BaseCommand

from apps.library.tasks import scan_library_task


class Command(BaseCommand):
    help = (
        "Queue a library scan on Celery instead of running it inline — used by "
        "docker/entrypoint.sh on `web` container start, so startup isn't blocked by "
        "however long scanning MUSIC_ROOT takes. Always queues a quick sync (add-only): a "
        "full sync's stale-row cleanup runs only on explicit staff request (the navbar "
        "'Library sync' menu, or `manage.py scan_library --full`), never automatically on "
        "boot — a MUSIC_ROOT that's temporarily unavailable at startup must never look like "
        "every file in it was deleted."
    )

    def handle(self, *args, **options):
        scan_library_task.delay(full=False)
        self.stdout.write(self.style.SUCCESS("Library scan queued."))
