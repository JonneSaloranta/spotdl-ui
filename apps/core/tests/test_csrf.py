"""CSRF protection is actually enforced on state-changing views
(CLAUDE.md #12/#25/#29). The default Django test Client disables CSRF
checks for convenience, so these tests opt back in explicitly with
`enforce_csrf_checks=True`."""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.downloader.models import DownloadBatch

pytestmark = pytest.mark.django_db


@pytest.fixture
def csrf_client():
    return Client(enforce_csrf_checks=True)


class TestCSRFEnforcement:
    def test_login_post_without_token_is_rejected(self, csrf_client):
        User.objects.create_user(username="alice", password="x")
        resp = csrf_client.post(reverse("accounts:login"), {"username": "alice", "password": "x"})
        assert resp.status_code == 403

    def test_batch_submit_without_token_is_rejected(self, csrf_client):
        user = User.objects.create_user(username="alice", password="x")
        csrf_client.force_login(user)
        resp = csrf_client.post(reverse("downloader:submit"), {"urls": "https://open.spotify.com/track/a"})
        assert resp.status_code == 403
        assert DownloadBatch.objects.count() == 0

    def test_theme_set_without_token_is_rejected(self, csrf_client):
        user = User.objects.create_user(username="alice", password="x")
        csrf_client.force_login(user)
        resp = csrf_client.post(reverse("accounts:set_theme"), {"theme": "dark"})
        assert resp.status_code == 403

    def test_request_with_valid_token_succeeds(self, csrf_client):
        """Sanity check: the above failures are really about the missing
        token, not something else broken about these views."""
        user = User.objects.create_user(username="alice", password="x")
        csrf_client.force_login(user)

        # Load a page to obtain a real CSRF cookie/token pair.
        get_resp = csrf_client.get(reverse("core:home"))
        token = get_resp.cookies["csrftoken"].value

        resp = csrf_client.post(
            reverse("downloader:submit"),
            {"urls": "https://open.spotify.com/track/a", "csrfmiddlewaretoken": token},
            HTTP_X_CSRFTOKEN=token,
        )
        assert resp.status_code == 302
        assert DownloadBatch.objects.count() == 1
