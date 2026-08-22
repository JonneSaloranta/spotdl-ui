"""Build the HTTP response that serves a TrackFile's audio or cover image.

Split out of apps.library.views so apps.sharing.views can reuse the exact
same response-building logic (X-Accel-Redirect vs. direct FileResponse;
reading embedded cover art) for a guest's token-scoped access to their own
shared-link downloads — the logic itself doesn't depend on *how* the
caller decided the request is authorized, only on which TrackFile to
serve, so it doesn't belong to either caller specifically.
"""

from __future__ import annotations

import mimetypes
import urllib.parse
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse

from apps.library.coverart import extract_cover_art
from apps.library.models import TrackFile


def build_stream_response(track: TrackFile) -> HttpResponse:
    """Stream `track`'s audio file for in-browser playback.

    Never takes a filesystem path from the caller: `track.path` is the
    server-sanitized path recorded at finalization time
    (apps/library/filenames.py), not user input — callers are only ever
    responsible for authorizing *which* TrackFile row to pass in here
    (CLAUDE.md #11).
    """
    content_type = track.mime_type or mimetypes.guess_type(track.filename)[0] or "application/octet-stream"

    if settings.MUSIC_STREAMING_BACKEND == "nginx":
        # Empty body: nginx replaces it with the real file (and handles
        # HTTP Range requests for seeking) once it sees this header — see
        # the "internal" /protected-music/ location in docker/nginx.conf.
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = "/protected-music/" + urllib.parse.quote(track.path)
        return response

    file_path = Path(settings.MUSIC_ROOT) / track.path
    if not file_path.is_file():
        raise Http404
    # Django's FileResponse handles Range requests itself since Django 3.0,
    # so seeking still works without nginx in front (e.g. local dev).
    return FileResponse(open(file_path, "rb"), content_type=content_type)


def build_cover_response(track: TrackFile) -> HttpResponse:
    """Serve `track`'s embedded cover image, read straight from its tags.

    404s (rather than serving a placeholder) when there's no embedded
    picture — callers are expected to treat a failed image load as "no
    cover" and fall back to an icon client-side, so a 404 here is the
    normal, expected case for a track with no artwork, not an error
    condition.
    """
    file_path = Path(settings.MUSIC_ROOT) / track.path
    cover = extract_cover_art(file_path) if file_path.is_file() else None
    if cover is None:
        raise Http404
    data, mime_type = cover
    response = HttpResponse(data, content_type=mime_type)
    # Private: every caller of this module gates access some way (login
    # for the library, a valid token for a shared link), so a shared
    # cache must not serve this to someone who hasn't been checked.
    response["Cache-Control"] = "private, max-age=86400"
    return response
