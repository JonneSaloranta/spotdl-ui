from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from apps.core.audit import log_event
from apps.core.forms import SiteSettingsForm
from apps.core.models import SiteSettings
from apps.downloader.models import DownloadBatch


@login_required
def home(request):
    """Main page: URL submission form plus the user's recent batches.

    The actual submission handling lives in apps.downloader.views; this view
    only renders the shell and the batches the current user can see.
    """
    batches = (
        DownloadBatch.objects.filter(created_by=request.user)
        .order_by("-created_at")[:10]
    )
    return render(request, "core/home.html", {"batches": batches})


@staff_member_required
def site_settings_view(request):
    """Simplified settings page (CLAUDE.md #11) — the handful of fields
    an operator adjusts day-to-day, as an alternative to the fuller
    Django admin change form. Deep administration (users, shared links,
    audit log, batches) intentionally stays in Django admin rather than
    being duplicated here."""
    instance = SiteSettings.load()
    if request.method == "POST":
        form = SiteSettingsForm(request.POST, instance=instance)
        if form.is_valid():
            form.save()
            log_event(
                "settings_changed", request=request, target=instance,
                detail={"changed_fields": form.changed_data},
            )
            messages.success(request, _("Settings saved."))
            return redirect(reverse("core:settings"))
    else:
        form = SiteSettingsForm(instance=instance)

    return render(request, "core/settings.html", {"form": form})


@require_GET
def manifest(request):
    """PWA web app manifest (CLAUDE.md #16). Generated from SiteSettings
    rather than a static file so branding changes apply without a
    deployment."""
    site_settings = SiteSettings.load()
    return JsonResponse({
        "name": site_settings.site_name,
        "short_name": site_settings.site_name[:12],
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#212529",
        "theme_color": "#0d6efd",
        "icons": [
            {"src": static("img/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": static("img/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }, content_type="application/manifest+json")


@require_GET
@cache_control(no_cache=True)  # browsers must always re-check for a new service worker
def service_worker(request):
    """Serves the service worker from the site root so its scope covers
    the whole app rather than only /static/ (CLAUDE.md #16)."""
    return render(request, "core/sw.js", content_type="application/javascript")


def offline(request):
    """Offline fallback shown by the service worker when a navigation
    fails with no network. Deliberately has no login_required and no
    per-user data — caching it must never leak private information."""
    return render(request, "core/offline.html")
