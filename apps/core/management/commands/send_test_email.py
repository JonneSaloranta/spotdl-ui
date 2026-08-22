from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a test email to verify SMTP configuration (CLAUDE.md #17)."

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="Email address to send the test message to.")

    def handle(self, *args, **options):
        recipient = options["recipient"]
        try:
            send_mail(
                subject="spotDL Web UI test email",
                message="This is a test email from your spotDL Web UI installation. "
                        "If you received this, SMTP is configured correctly.",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
        except Exception as exc:
            raise CommandError(f"Could not send test email: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Test email sent to {recipient}."))
