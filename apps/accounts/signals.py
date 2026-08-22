from django.conf import settings
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.accounts.models import UserProfile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    """Ensure every user has a profile row, including users created via
    the Django admin or `createsuperuser` rather than the app's own signup
    flow (which does not exist yet — see CLAUDE.md #18)."""
    if created:
        UserProfile.objects.get_or_create(user=instance)


# Security event logging (CLAUDE.md #14). Connected here rather than in
# views.py so every login path — the app's own form, Django admin's login,
# any future API auth — is covered by one place, not re-implemented per view.

@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    from apps.core.audit import log_event

    log_event("login", request=request, actor=user, target=user)


@receiver(user_logged_out)
def log_logout(sender, request, user, **kwargs):
    from apps.core.audit import log_event

    if user is not None:  # None if the session was already anonymous
        log_event("logout", request=request, actor=user, target=user)


@receiver(user_login_failed)
def log_login_failed(sender, credentials, request, **kwargs):
    from apps.core.audit import log_event

    # Never log the attempted password — only the username that was tried.
    username = credentials.get("username", "")
    log_event("login_failed", request=request, detail={"username": username})
