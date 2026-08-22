from django.shortcuts import render

from apps.core.models import SiteSettings

# Paths that must stay reachable even during maintenance mode: health checks
# (used by Docker/orchestrator probes) and staff login (so an administrator
# can turn maintenance mode back off).
_EXEMPT_PREFIXES = ("/health/", "/admin/login/")


class MaintenanceModeMiddleware:
    """Show a maintenance page to non-staff users when enabled in SiteSettings."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(_EXEMPT_PREFIXES):
            return self.get_response(request)

        settings_obj = SiteSettings.load()
        user = getattr(request, "user", None)
        is_staff = bool(user and user.is_authenticated and user.is_staff)
        if settings_obj.maintenance_mode and not is_staff:
            return render(
                request,
                "core/maintenance.html",
                {"message": settings_obj.maintenance_message},
                status=503,
            )
        return self.get_response(request)
