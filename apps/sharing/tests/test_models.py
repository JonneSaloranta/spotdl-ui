import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from apps.sharing.models import SharedImportLink

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    return User.objects.create_user(username="admin", password="x", is_staff=True)


def make_link(admin_user, **kwargs):
    kwargs.setdefault("ttl_hours", 24)
    kwargs.setdefault("maximum_uses", 1)
    return SharedImportLink.generate(created_by=admin_user, **kwargs)


class TestSharedImportLinkGenerate:
    def test_raw_token_is_not_stored(self, admin_user):
        link, raw_token = make_link(admin_user)
        assert link.token_hash != raw_token
        assert len(raw_token) > 20

    def test_lookup_by_raw_token_succeeds(self, admin_user):
        link, raw_token = make_link(admin_user)
        found = SharedImportLink.get_valid_by_token(raw_token)
        assert found is not None
        assert found.pk == link.pk

    def test_lookup_with_wrong_token_fails(self, admin_user):
        make_link(admin_user)
        assert SharedImportLink.get_valid_by_token("not-the-real-token") is None

    def test_lookup_with_empty_token_fails(self):
        assert SharedImportLink.get_valid_by_token("") is None
        assert SharedImportLink.get_valid_by_token(None) is None


class TestSharedImportLinkValidity:
    def test_expired_link_is_invalid(self, admin_user):
        link, raw_token = make_link(admin_user, maximum_uses=5)
        link.expires_at = timezone.now() - timezone.timedelta(hours=1)
        link.save()
        assert link.is_expired is True
        assert link.is_valid is False
        assert SharedImportLink.get_valid_by_token(raw_token) is None

    def test_disabled_link_is_invalid(self, admin_user):
        link, raw_token = make_link(admin_user, maximum_uses=5)
        link.enabled = False
        link.save()
        assert link.is_valid is False
        assert SharedImportLink.get_valid_by_token(raw_token) is None

    def test_exhausted_link_is_invalid(self, admin_user):
        link, raw_token = make_link(admin_user, maximum_uses=1)
        link.register_use()
        assert link.uses == 1
        assert link.is_exhausted is True
        assert link.is_valid is False
        assert SharedImportLink.get_valid_by_token(raw_token) is None

    def test_link_with_remaining_uses_is_valid(self, admin_user):
        link, raw_token = make_link(admin_user, maximum_uses=3)
        link.register_use()
        assert link.is_valid is True
        assert SharedImportLink.get_valid_by_token(raw_token) is not None


class TestSharedImportLinkViewability:
    """is_viewable (and get_viewable_by_token) deliberately ignore the
    used-up-submissions case — a guest should still be able to see and
    stream what they already downloaded through an exhausted link."""

    def test_exhausted_link_is_still_viewable(self, admin_user):
        link, raw_token = make_link(admin_user, maximum_uses=1)
        link.register_use()
        assert link.is_exhausted is True
        assert link.is_viewable is True
        assert SharedImportLink.get_viewable_by_token(raw_token) is not None

    def test_expired_link_is_not_viewable(self, admin_user):
        link, raw_token = make_link(admin_user, maximum_uses=5)
        link.expires_at = timezone.now() - timezone.timedelta(hours=1)
        link.save()
        assert link.is_viewable is False
        assert SharedImportLink.get_viewable_by_token(raw_token) is None

    def test_disabled_link_is_not_viewable(self, admin_user):
        link, raw_token = make_link(admin_user, maximum_uses=5)
        link.enabled = False
        link.save()
        assert link.is_viewable is False
        assert SharedImportLink.get_viewable_by_token(raw_token) is None

    def test_fresh_link_is_both_valid_and_viewable(self, admin_user):
        link, _raw_token = make_link(admin_user, maximum_uses=5)
        assert link.is_valid is True
        assert link.is_viewable is True
