"""URL configuration for the spotDL Web UI project.

Language is never reflected in the URL (no /en/, /fi/ prefix) — it is
selected purely via a cookie/session, set by the language-switcher form
(django.views.i18n.set_language) and otherwise detected from the
Accept-Language header. This keeps every URL stable across a language
switch, which matters for shared links and bookmarks. See
apps.core.middleware for how the active language is actually resolved.
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core import views as core_views

urlpatterns = [
    path("", include("apps.core.health_urls")),
    path("i18n/", include("django.conf.urls.i18n")),
    # Fixed, language-independent paths: PWA manifest/service-worker
    # registration depends on a stable URL (CLAUDE.md #16).
    path("manifest.webmanifest", core_views.manifest, name="manifest"),
    path("sw.js", core_views.service_worker, name="service_worker"),
    path("admin/", admin.site.urls),
    path("accounts/", include("apps.accounts.urls")),
    path("share/", include("apps.sharing.urls")),
    path("library/", include("apps.library.urls")),
    path("library/", include("apps.musicbrainz.urls")),
    path("", include("apps.downloader.urls")),
    path("", include("apps.core.urls")),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # `runserver` serves /static/ automatically via its own magic even
    # without this, but that bypasses normal URL resolution — the Django
    # test Client (and anything else hitting the WSGI app directly rather
    # than through `runserver`) needs it wired explicitly to see static
    # files at all. In production DEBUG is false and nginx serves
    # /static/ directly instead (see docker/nginx.conf).
    urlpatterns += staticfiles_urlpatterns()
