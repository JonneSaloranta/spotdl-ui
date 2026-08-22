import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestAuditLogging:
    def test_successful_login_is_logged(self, client: Client):
        User.objects.create_user(username="alice", password="correct-horse")
        client.post(reverse("accounts:login"), {"username": "alice", "password": "correct-horse"})
        entry = AuditLog.objects.get(action=AuditLog.Action.LOGIN)
        assert entry.actor.username == "alice"

    def test_failed_login_is_logged_without_password(self, client: Client):
        User.objects.create_user(username="alice", password="correct-horse")
        client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        entry = AuditLog.objects.get(action=AuditLog.Action.LOGIN_FAILED)
        assert entry.detail["username"] == "alice"
        assert "wrong" not in str(entry.detail)
        assert entry.actor is None

    def test_logout_is_logged(self, client: Client):
        user = User.objects.create_user(username="alice", password="correct-horse")
        client.force_login(user)
        client.post(reverse("accounts:logout"))
        assert AuditLog.objects.filter(action=AuditLog.Action.LOGOUT, actor=user).exists()


class TestLoginRateLimiting:
    def test_allows_attempts_under_the_limit(self, client: Client):
        User.objects.create_user(username="alice", password="correct-horse")
        for _ in range(5):
            resp = client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
            assert resp.status_code == 200  # re-rendered form with an error, not rate-limited

    def test_blocks_after_too_many_attempts(self, client: Client):
        User.objects.create_user(username="alice", password="correct-horse")
        for _ in range(10):
            client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        resp = client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        assert resp.status_code == 429

    def test_rate_limit_also_applies_to_get(self, client: Client):
        for _ in range(11):
            resp = client.get(reverse("accounts:login"))
        assert resp.status_code == 429

    def test_correct_password_still_blocked_once_rate_limited(self, client: Client):
        User.objects.create_user(username="alice", password="correct-horse")
        for _ in range(10):
            client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        resp = client.post(reverse("accounts:login"), {"username": "alice", "password": "correct-horse"})
        assert resp.status_code == 429


class TestPasswordResetRateLimiting:
    def test_allows_requests_under_the_limit(self, client: Client):
        for _ in range(4):
            resp = client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
            assert resp.status_code == 302

    def test_blocks_after_too_many_requests(self, client: Client):
        for _ in range(5):
            client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        resp = client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        assert resp.status_code == 429

    def test_cannot_be_used_to_spam_a_single_address_past_the_limit(self, client: Client, settings):
        # The rate limit is IP-based, not address-based — confirms varying
        # the target email doesn't bypass it.
        for i in range(5):
            client.post(reverse("accounts:password_reset"), {"email": f"target{i}@example.com"})
        resp = client.post(reverse("accounts:password_reset"), {"email": "target-final@example.com"})
        assert resp.status_code == 429

    def test_login_and_password_reset_limits_are_independent(self, client: Client):
        # Exhausting one shouldn't exhaust the other — they're different
        # rate-limit keys even though both are keyed by the same IP.
        User.objects.create_user(username="alice", password="correct-horse")
        for _ in range(5):
            client.post(reverse("accounts:password_reset"), {"email": "nobody@example.com"})
        resp = client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        assert resp.status_code == 200
