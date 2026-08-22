"""Helper for recording security/administrative events (CLAUDE.md #14)."""

from __future__ import annotations

from typing import Any

from apps.core.models import AuditLog


def client_ip(request) -> str | None:
    """Best-effort client IP, honoring X-Forwarded-For behind the nginx
    reverse proxy. Shared by audit logging and rate limiting so both agree
    on the same address for the same request."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_event(
    action: str,
    *,
    request=None,
    actor=None,
    target=None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    """Record an AuditLog entry.

    Never pass secrets (passwords, tokens, session keys) in `detail` — this
    table is readable from the Django admin by any staff user.
    """
    if actor is None and request is not None:
        user = getattr(request, "user", None)
        actor = user if (user is not None and user.is_authenticated) else None

    return AuditLog.objects.create(
        actor=actor,
        action=action,
        ip_address=client_ip(request) if request is not None else None,
        target_repr=str(target) if target is not None else "",
        detail=detail or {},
    )
