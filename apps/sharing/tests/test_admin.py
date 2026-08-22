import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from apps.core.models import AuditLog
from apps.sharing.models import SharedImportLink

pytestmark = pytest.mark.django_db


@pytest.fixture
def staff_client():
    user = User.objects.create_user(username="staffer", password="x", is_staff=True, is_superuser=True)
    c = Client()
    c.force_login(user)
    return c, user


class TestCreateLinkView:
    def test_requires_staff(self, client: Client):
        User.objects.create_user(username="regular", password="x")
        client.login(username="regular", password="x")
        resp = client.get(reverse("admin:sharing_sharedimportlink_create"))
        assert resp.status_code in (302, 403)

    def test_get_shows_the_form(self, staff_client):
        c, _user = staff_client
        resp = c.get(reverse("admin:sharing_sharedimportlink_create"))
        assert resp.status_code == 200
        assert b'name="ttl_hours"' in resp.content
        assert b'name="maximum_uses"' in resp.content
        assert b'name="max_items_per_submission"' in resp.content

    def test_stock_add_view_is_disabled(self, staff_client):
        # The regular Django admin "Add" form can't populate token_hash
        # (it's a hash with no field of its own) — creation only ever
        # happens through create_link.
        c, _user = staff_client
        resp = c.get(reverse("admin:sharing_sharedimportlink_add"))
        assert resp.status_code == 403

    def test_valid_submission_creates_a_link_and_shows_the_token_once(self, staff_client):
        c, user = staff_client
        resp = c.post(reverse("admin:sharing_sharedimportlink_create"), {
            "label": "Jane's invite", "ttl_hours": 24, "maximum_uses": 3, "max_items_per_submission": 10,
        })
        assert resp.status_code == 200
        link = SharedImportLink.objects.get(label="Jane's invite")
        assert link.created_by == user
        assert link.maximum_uses == 3
        assert link.max_items_per_submission == 10
        # The generated share URL (containing the raw token) is shown exactly here.
        assert b"/share/" in resp.content

    def test_valid_submission_logs_an_audit_event(self, staff_client):
        c, _user = staff_client
        c.post(reverse("admin:sharing_sharedimportlink_create"), {
            "label": "", "ttl_hours": 24, "maximum_uses": 1, "max_items_per_submission": 5,
        })
        assert AuditLog.objects.filter(action="shared_link_created").exists()

    def test_ttl_over_the_site_maximum_is_rejected(self, staff_client, settings):
        settings.SHARED_LINK_MAX_TTL_HOURS = 48
        c, _user = staff_client
        resp = c.post(reverse("admin:sharing_sharedimportlink_create"), {
            "label": "", "ttl_hours": 999, "maximum_uses": 1, "max_items_per_submission": 5,
        })
        assert resp.status_code == 200
        assert SharedImportLink.objects.count() == 0

    def test_maximum_uses_over_the_site_maximum_is_rejected(self, staff_client, settings):
        settings.SHARED_LINK_MAX_USES = 5
        c, _user = staff_client
        resp = c.post(reverse("admin:sharing_sharedimportlink_create"), {
            "label": "", "ttl_hours": 24, "maximum_uses": 999, "max_items_per_submission": 5,
        })
        assert resp.status_code == 200
        assert SharedImportLink.objects.count() == 0

    def test_max_items_per_submission_over_the_site_maximum_is_rejected(self, staff_client, settings):
        settings.SHARED_LINK_MAX_ITEMS_PER_SUBMISSION = 10
        c, _user = staff_client
        resp = c.post(reverse("admin:sharing_sharedimportlink_create"), {
            "label": "", "ttl_hours": 24, "maximum_uses": 1, "max_items_per_submission": 999,
        })
        assert resp.status_code == 200
        assert SharedImportLink.objects.count() == 0


class TestChangeListView:
    def test_shows_a_create_link_button(self, staff_client):
        c, _user = staff_client
        resp = c.get(reverse("admin:sharing_sharedimportlink_changelist"))
        assert resp.status_code == 200
        assert reverse("admin:sharing_sharedimportlink_create").encode() in resp.content

    def test_enabled_flag_remains_editable(self, staff_client):
        c, user = staff_client
        link, _raw_token = SharedImportLink.generate(created_by=user, ttl_hours=24, maximum_uses=1)
        resp = c.get(reverse("admin:sharing_sharedimportlink_change", args=[link.pk]))
        assert resp.status_code == 200
        assert b'name="enabled"' in resp.content
