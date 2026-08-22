from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, PasswordResetView
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from apps.accounts.models import UserProfile
from apps.core.audit import client_ip
from apps.core.ratelimit import is_rate_limited

_VALID_THEMES = {choice for choice, _label in UserProfile.Theme.choices}


class RateLimitedViewMixin:
    """Rejects further requests from an IP once it's made too many within
    a window, regardless of outcome (CLAUDE.md #20: rate limiting for
    authentication). Checked in dispatch() so it covers GET (viewing the
    form) as well as POST (the actual attempt/submission).

    Subclasses set `rate_limit_key`, `rate_limit_max`, `rate_limit_window_seconds`.
    """

    rate_limit_key: str
    rate_limit_max: int
    rate_limit_window_seconds: int

    def dispatch(self, request, *args, **kwargs):
        ip = client_ip(request) or "unknown"
        if is_rate_limited(
            f"{self.rate_limit_key}:{ip}",
            limit=self.rate_limit_max,
            window_seconds=self.rate_limit_window_seconds,
        ):
            return HttpResponse("Too many requests. Please try again later.", status=429)
        return super().dispatch(request, *args, **kwargs)


class RateLimitedLoginView(RateLimitedViewMixin, LoginView):
    # Deliberately coarse: enough to slow down credential-stuffing without
    # locking out a real user who mistypes a password a few times.
    rate_limit_key = "login"
    rate_limit_max = 10
    rate_limit_window_seconds = 300


class RateLimitedPasswordResetView(RateLimitedViewMixin, PasswordResetView):
    # Tighter than login: a reset sends an email, so this also guards
    # against using the site to spam an arbitrary address.
    rate_limit_key = "password_reset"
    rate_limit_max = 5
    rate_limit_window_seconds = 300


@login_required
@require_POST
def set_theme(request):
    """Persist the caller's theme preference (CLAUDE.md #14).

    Also mirrored into a plain cookie so the *next* page load can set
    `data-bs-theme` server-side before any CSS/JS runs, avoiding a flash
    of the wrong theme.
    """
    theme = request.POST.get("theme")
    if theme not in _VALID_THEMES:
        return HttpResponseBadRequest("invalid theme")

    profile = request.user.profile
    profile.theme_preference = theme
    profile.save(update_fields=["theme_preference", "updated_at"])

    response = JsonResponse({"theme": theme})
    response.set_cookie(
        "theme", theme, max_age=60 * 60 * 24 * 365, samesite="Lax",
    )
    return response


@login_required
def profile(request):
    return render(request, "accounts/profile.html", {"profile": request.user.profile})
