import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog, SiteSettings

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    # SiteSettings.load() caches the singleton row; without clearing this
    # between tests, a cached instance from an earlier (already
    # rolled-back) test's transaction can leak into this one and make
    # direct SiteSettings.objects.get() calls here see a different state
    # than .load() does.
    cache.clear()
    yield
    cache.clear()


def valid_post_data(**overrides):
    data = {
        "site_name": "New Name",
        "maintenance_message": "",
        "default_duplicate_policy": "skip",
        "max_urls_per_batch": 10,
        "musicbrainz_sweep_batch_size": 5,
        "primary_audio_provider": "youtube-music",
        "primary_provider_attempts": 3,
        # fallback_audio_providers omitted: it's a required=False multi-select
    }
    data.update(overrides)
    return data


@pytest.fixture
def staff_client():
    user = User.objects.create_user(username="staff", password="x", is_staff=True)
    c = Client()
    c.force_login(user)
    return c


@pytest.fixture
def normal_client():
    user = User.objects.create_user(username="user", password="x")
    c = Client()
    c.force_login(user)
    return c


class TestAccess:
    def test_requires_login(self, client: Client):
        resp = client.get(reverse("core:settings"))
        assert resp.status_code == 302

    def test_requires_staff(self, normal_client):
        resp = normal_client.get(reverse("core:settings"))
        assert resp.status_code in (302, 403)

    def test_accessible_to_staff(self, staff_client):
        resp = staff_client.get(reverse("core:settings"))
        assert resp.status_code == 200


class TestSaving:
    def test_updates_site_settings(self, staff_client):
        resp = staff_client.post(reverse("core:settings"), valid_post_data())
        assert resp.status_code == 302
        settings_obj = SiteSettings.load()
        assert settings_obj.site_name == "New Name"
        assert settings_obj.default_duplicate_policy == "skip"
        assert settings_obj.max_urls_per_batch == 10
        assert settings_obj.maintenance_mode is False  # unchecked checkbox

    def test_records_audit_log_with_changed_fields(self, staff_client):
        staff_client.post(reverse("core:settings"), valid_post_data(site_name="Renamed"))
        entry = AuditLog.objects.get(action=AuditLog.Action.SETTINGS_CHANGED)
        assert "site_name" in entry.detail["changed_fields"]

    def test_invalid_submission_shows_errors_without_saving(self, staff_client):
        original_name = SiteSettings.load().site_name
        resp = staff_client.post(reverse("core:settings"), valid_post_data(site_name=""))
        assert resp.status_code == 200
        assert SiteSettings.load().site_name == original_name

    def test_updates_primary_audio_provider_and_attempts(self, staff_client):
        staff_client.post(reverse("core:settings"), valid_post_data(
            primary_audio_provider="soundcloud", primary_provider_attempts=5,
        ))
        settings_obj = SiteSettings.load()
        assert settings_obj.primary_audio_provider == "soundcloud"
        assert settings_obj.primary_provider_attempts == 5

    def test_updates_fallback_audio_providers_as_a_list(self, staff_client):
        data = valid_post_data()
        data["fallback_audio_providers"] = ["soundcloud", "bandcamp"]
        staff_client.post(reverse("core:settings"), data)
        settings_obj = SiteSettings.load()
        assert settings_obj.fallback_audio_providers_list() == ["soundcloud", "bandcamp"]

    def test_no_fallback_providers_selected_clears_the_list(self, staff_client):
        settings_obj = SiteSettings.load()
        settings_obj.set_fallback_audio_providers_list(["soundcloud"])
        settings_obj.save()

        staff_client.post(reverse("core:settings"), valid_post_data())  # no fallback_audio_providers key

        assert SiteSettings.load().fallback_audio_providers_list() == []

    def test_form_prefills_existing_fallback_providers_on_get(self, staff_client):
        settings_obj = SiteSettings.load()
        settings_obj.set_fallback_audio_providers_list(["bandcamp"])
        settings_obj.save()

        resp = staff_client.get(reverse("core:settings"))
        assert resp.context["form"].fields["fallback_audio_providers"].initial == ["bandcamp"]
