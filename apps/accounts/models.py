from django.conf import settings
from django.db import models


class UserProfile(models.Model):
    """Optional per-user preferences (CLAUDE.md #6)."""

    class Theme(models.TextChoices):
        AUTO = "auto", "Automatic"
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    preferred_language = models.CharField(
        max_length=10, choices=settings.LANGUAGES, blank=True,
        help_text="Empty means use the site default.",
    )
    theme_preference = models.CharField(max_length=10, choices=Theme.choices, default=Theme.AUTO)
    timezone = models.CharField(max_length=64, default="UTC")
    notify_on_batch_complete = models.BooleanField(default=True)
    notify_on_batch_failed = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile<{self.user}>"
