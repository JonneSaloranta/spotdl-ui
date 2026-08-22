"""PWA assets (CLAUDE.md #16/#25)."""

import json

import pytest
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestManifest:
    def test_returns_valid_json_manifest(self, client: Client):
        resp = client.get(reverse("manifest"))
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/manifest+json"
        data = json.loads(resp.content)
        assert data["name"]
        assert data["display"] == "standalone"
        assert data["start_url"] == "/"
        assert len(data["icons"]) >= 2

    def test_reflects_site_name(self, client: Client):
        from apps.core.models import SiteSettings

        settings_obj = SiteSettings.load()
        settings_obj.site_name = "My Custom Name"
        settings_obj.save()

        resp = client.get(reverse("manifest"))
        data = json.loads(resp.content)
        assert data["name"] == "My Custom Name"


class TestServiceWorker:
    def test_is_served_as_javascript(self, client: Client):
        resp = client.get(reverse("service_worker"))
        assert resp.status_code == 200
        assert "javascript" in resp["Content-Type"]

    def test_is_served_from_the_site_root(self):
        # Scope matters: a service worker registered from /static/sw.js
        # would only ever control /static/ pages. It must be reachable at
        # the bare root path, not nested under /static/.
        resp = Client().get("/sw.js")
        assert resp.status_code == 200

    def test_precaches_the_offline_page_and_no_authenticated_paths(self, client: Client):
        resp = client.get(reverse("service_worker"))
        content = resp.content.decode()
        assert '"/offline/"' in content
        # Checked as quoted string literals (i.e. actually listed for
        # precaching), not a bare substring search — the file's own
        # comments legitimately mention these paths as examples of what
        # is *not* cached.
        for private_path in ('"/accounts/', '"/admin/', '"/batches/', '"/share/'):
            assert private_path not in content

    def test_is_not_cached_by_the_browser(self, client: Client):
        # A stale cached service worker script would never pick up updates.
        resp = client.get(reverse("service_worker"))
        assert "no-cache" in resp.get("Cache-Control", "")


class TestOfflinePage:
    def test_renders_without_login(self, client: Client):
        resp = client.get(reverse("core:offline"))
        assert resp.status_code == 200

    def test_contains_no_user_specific_data(self, client: Client):
        # The offline shell is cached by the service worker and shown to
        # anyone offline — it must never leak session-specific content.
        resp = client.get(reverse("core:offline"))
        assert b"batch" not in resp.content.lower()


class TestIcons:
    def test_icon_paths_resolve_via_staticfiles_finders(self):
        # Django's test runner forces DEBUG=False, which stops the app
        # from serving /static/ itself (production relies on nginx for
        # that instead — see docker/nginx.conf) — so this checks the
        # underlying files are present and findable rather than hitting
        # the URL end-to-end.
        from django.contrib.staticfiles import finders

        for path in ("img/icon-192.png", "img/icon-512.png", "img/favicon-32.png"):
            assert finders.find(path) is not None, path
