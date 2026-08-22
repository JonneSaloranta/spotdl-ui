from django.conf import settings

from apps.core.models import SiteSettings


def site_settings(request):
    """Expose non-secret SiteSettings fields (plus the app version, shown in
    the footer — useful for support/troubleshooting) to every template."""
    return {"site_settings": SiteSettings.load(), "app_version": settings.APP_VERSION}
