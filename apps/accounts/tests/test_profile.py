import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.accounts.models import UserProfile

pytestmark = pytest.mark.django_db


def test_profile_is_auto_created_for_new_user():
    user = User.objects.create_user(username="alice", password="x")
    assert UserProfile.objects.filter(user=user).exists()
    assert user.profile.theme_preference == UserProfile.Theme.AUTO


def test_profile_is_not_duplicated_on_subsequent_saves():
    user = User.objects.create_user(username="alice", password="x")
    user.email = "alice@example.com"
    user.save()
    assert UserProfile.objects.filter(user=user).count() == 1


class TestSetThemeView:
    def test_requires_login(self, client: Client):
        resp = client.post(reverse("accounts:set_theme"), {"theme": "dark"})
        assert resp.status_code == 302

    def test_persists_valid_theme(self):
        user = User.objects.create_user(username="alice", password="x")
        c = Client()
        c.force_login(user)
        resp = c.post(reverse("accounts:set_theme"), {"theme": "dark"})
        assert resp.status_code == 200
        user.profile.refresh_from_db()
        assert user.profile.theme_preference == "dark"
        assert resp.cookies["theme"].value == "dark"

    def test_rejects_invalid_theme(self):
        user = User.objects.create_user(username="alice", password="x")
        c = Client()
        c.force_login(user)
        resp = c.post(reverse("accounts:set_theme"), {"theme": "not-a-real-theme"})
        assert resp.status_code == 400
        user.profile.refresh_from_db()
        assert user.profile.theme_preference == UserProfile.Theme.AUTO

    def test_get_not_allowed(self):
        user = User.objects.create_user(username="alice", password="x")
        c = Client()
        c.force_login(user)
        resp = c.get(reverse("accounts:set_theme"))
        assert resp.status_code == 405
