"""Baseline accessibility checks (CLAUDE.md #13: "keyboard navigation,
focus states and semantic HTML"). Not exhaustive — just locks in the
handful of concrete things that were added deliberately, so a future
template edit doesn't silently drop them."""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_skip_link_is_present_and_targets_main_content(client: Client):
    resp = client.get(reverse("accounts:login"))
    content = resp.content.decode()
    assert 'href="#main-content"' in content
    assert 'id="main-content"' in content


def test_navbar_toggler_has_accessible_label(client: Client):
    resp = client.get(reverse("accounts:login"))
    assert b'aria-label="Toggle navigation"' in resp.content


def test_batch_status_summary_is_a_live_region():
    user = User.objects.create_user(username="alice", password="x")
    from apps.downloader.models import DownloadBatch

    batch = DownloadBatch.objects.create(created_by=user, source_urls=["https://open.spotify.com/track/x"])
    c = Client()
    c.force_login(user)
    resp = c.get(reverse("downloader:batch_detail", args=[batch.pk]))
    assert b'aria-live="polite"' in resp.content
