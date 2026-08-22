"""Liveness/readiness endpoints for Docker/orchestrator healthchecks.

Unauthenticated and deliberately minimal: they must never reveal
configuration, stack traces or credentials (CLAUDE.md security checklist).
"""

import logging

from django.core.cache import cache
from django.db import connections
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
@never_cache
def liveness(request):
    """Process is up and serving requests. Does not touch dependencies."""
    return JsonResponse({"status": "ok"})


@require_GET
@never_cache
def readiness(request):
    """Process can serve real traffic: database and cache are reachable."""
    checks = {"database": _check_database(), "cache": _check_cache()}
    healthy = all(checks.values())
    return JsonResponse({"status": "ok" if healthy else "unavailable", "checks": checks}, status=200 if healthy else 503)


def _check_database() -> bool:
    try:
        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
        return True
    except OperationalError:
        logger.exception("Readiness check: database unreachable")
        return False


def _check_cache() -> bool:
    try:
        cache.set("health:ready:probe", "1", timeout=5)
        return cache.get("health:ready:probe") == "1"
    except Exception:
        logger.exception("Readiness check: cache unreachable")
        return False
