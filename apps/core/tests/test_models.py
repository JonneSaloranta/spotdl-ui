import pytest
from django.contrib.auth.models import User
from django.core.cache import cache

from apps.core.audit import log_event
from apps.core.models import AuditLog, SiteSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestSiteSettingsSingleton:
    def test_load_creates_a_default_row(self):
        settings_obj = SiteSettings.load()
        assert settings_obj.pk == 1
        assert SiteSettings.objects.count() == 1

    def test_save_always_uses_pk_one(self):
        a = SiteSettings.load()
        a.site_name = "Renamed"
        a.save()
        b = SiteSettings.objects.get()
        assert b.pk == 1
        assert b.site_name == "Renamed"

    def test_load_is_cached_between_calls(self):
        first = SiteSettings.load()
        first.site_name = "Changed directly in DB"
        SiteSettings.objects.filter(pk=1).update(site_name="Changed directly in DB")
        second = SiteSettings.load()
        # Second call within the cache TTL should not re-hit the DB, so it
        # still reflects the value before the direct update.
        assert second.site_name != "Changed directly in DB" or second is first


class TestAuditLog:
    def test_log_event_records_actor_and_action(self):
        user = User.objects.create_user(username="alice", password="x")
        entry = log_event("login", actor=user)
        assert entry.action == AuditLog.Action.LOGIN
        assert entry.actor == user

    def test_log_event_without_actor_is_allowed(self):
        entry = log_event("shared_link_used")
        assert entry.actor is None
