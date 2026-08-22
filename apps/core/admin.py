from django.conf import settings
from django.contrib import admin, messages
from django.core.mail import send_mail
from django.shortcuts import redirect
from django.urls import path

from apps.core.models import AuditLog, SiteSettings


@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    """Only ever one row; hide the add/delete actions accordingly."""

    change_list_template = "admin/core/sitesettings/change_list.html"

    def has_add_permission(self, request):
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        custom_urls = [
            path("send-test-email/", self.admin_site.admin_view(self.send_test_email), name="core_sitesettings_test_email"),
        ]
        return custom_urls + super().get_urls()

    def send_test_email(self, request):
        """Admin "Send test email" function (CLAUDE.md #17)."""
        recipient = request.user.email
        if not recipient:
            messages.error(request, "Your admin account has no email address configured.")
            return redirect("admin:core_sitesettings_changelist")

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
            messages.error(request, f"Could not send test email: {exc}")
        else:
            messages.success(request, f"Test email sent to {recipient}.")
        return redirect("admin:core_sitesettings_changelist")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "target_repr", "ip_address")
    list_filter = ("action",)
    search_fields = ("target_repr", "actor__username")
    readonly_fields = [f.name for f in AuditLog._meta.fields]
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
