import pytest
from django.core.cache import cache

from apps.core.ratelimit import is_rate_limited

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_allows_requests_under_the_limit():
    for _ in range(3):
        assert is_rate_limited("k1", limit=3, window_seconds=60) is False


def test_blocks_requests_over_the_limit():
    for _ in range(3):
        is_rate_limited("k2", limit=3, window_seconds=60)
    assert is_rate_limited("k2", limit=3, window_seconds=60) is True


def test_keys_are_independent():
    for _ in range(5):
        is_rate_limited("k3a", limit=3, window_seconds=60)
    assert is_rate_limited("k3b", limit=3, window_seconds=60) is False
