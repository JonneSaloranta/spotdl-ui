"""Minimal cache-backed rate limiting (CLAUDE.md #20).

Deliberately simple (a fixed-window counter in Redis via django-cache)
rather than pulling in a dedicated package — the limits involved (shared
links, auth attempts) are coarse-grained and don't need a sliding window.
"""

from django.core.cache import cache


def is_rate_limited(key: str, *, limit: int, window_seconds: int) -> bool:
    """Return True if `key` has already been used `limit` or more times
    within the current `window_seconds` window, incrementing its counter
    as a side effect.

    Uses cache.add/incr rather than get+set so concurrent requests racing
    on the same key cannot both slip through under the limit.
    """
    cache_key = f"ratelimit:{key}"
    added = cache.add(cache_key, 1, timeout=window_seconds)
    if added:
        return False
    try:
        current = cache.incr(cache_key)
    except ValueError:
        # Key expired between add() and incr(); treat as a fresh window.
        cache.add(cache_key, 1, timeout=window_seconds)
        return False
    return current > limit
