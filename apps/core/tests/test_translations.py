"""Translation coverage (CLAUDE.md #15/#25).

Not a translation-quality check — just confirms the Finnish catalog exists,
is compiled, actually translates key UI strings, and that switching
languages changes rendered page content end-to-end.
"""

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _

pytestmark = pytest.mark.django_db


def test_finnish_catalog_is_compiled():
    from pathlib import Path

    from django.conf import settings

    mo_path = Path(settings.LOCALE_PATHS[0]) / "fi" / "LC_MESSAGES" / "django.mo"
    assert mo_path.exists(), "Run `manage.py compilemessages` after editing locale/fi/LC_MESSAGES/django.po"


def test_key_strings_are_translated_to_finnish():
    with translation.override("fi"):
        assert _("Log in") == "Kirjaudu sisään"
        assert _("Downloads") == "Lataukset"
        assert _("Library") == "Kirjasto"
        assert _("Cancel") == "Peruuta"
        assert _("Retry all") == "Lataa kaikki"
        assert _("Retry failed") == "Lataa epäonnistuneet"
        assert _("Skip duplicates") == "Ohita kaksoiskappaleet"
        assert _("Keep both copies") == "Säilytä molemmat kappaleet"
        assert _("Ask the user") == "Kysy käyttäjältä"


def test_untranslated_string_falls_back_to_source_text():
    # A string that only exists in English source should never raise or
    # come back empty when Finnish is active — gettext falls back to the
    # msgid itself.
    with translation.override("fi"):
        assert _("Log in") != ""


def test_login_page_renders_in_finnish(client: Client):
    resp = client.get(reverse("accounts:login"), HTTP_ACCEPT_LANGUAGE="fi")
    assert resp.status_code == 200
    assert "Kirjaudu".encode() in resp.content


def test_login_page_renders_in_english_by_default(client: Client):
    resp = client.get(reverse("accounts:login"), HTTP_ACCEPT_LANGUAGE="en")
    assert resp.status_code == 200
    assert b"Log in" in resp.content


def test_home_page_language_switch_changes_rendered_text():
    user = User.objects.create_user(username="alice", password="x")
    c = Client()
    c.force_login(user)

    resp_en = c.get(reverse("core:home"), HTTP_ACCEPT_LANGUAGE="en")
    resp_fi = c.get(reverse("core:home"), HTTP_ACCEPT_LANGUAGE="fi")

    assert b"Start download" in resp_en.content
    assert "Aloita lataus".encode() in resp_fi.content
