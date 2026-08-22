import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db


class TestManagementCommand:
    def test_sends_a_test_email(self):
        call_command("send_test_email", "someone@example.com")
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["someone@example.com"]


class TestAdminView:
    def test_requires_staff(self, client: Client):
        user = User.objects.create_user(username="user", password="x", email="user@example.com")
        client.force_login(user)
        resp = client.get(reverse("admin:core_sitesettings_test_email"))
        assert resp.status_code in (302, 403)
        assert len(mail.outbox) == 0

    def test_sends_email_to_staff_users_address(self, client: Client):
        staff = User.objects.create_user(
            username="admin", password="x", email="admin@example.com", is_staff=True,
        )
        client.force_login(staff)
        resp = client.get(reverse("admin:core_sitesettings_test_email"))
        assert resp.status_code == 302
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["admin@example.com"]

    def test_errors_when_staff_user_has_no_email(self, client: Client):
        staff = User.objects.create_user(username="admin", password="x", email="", is_staff=True)
        client.force_login(staff)
        resp = client.get(reverse("admin:core_sitesettings_test_email"))
        assert resp.status_code == 302
        assert len(mail.outbox) == 0
