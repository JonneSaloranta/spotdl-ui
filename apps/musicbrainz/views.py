"""MusicBrainz metadata review/apply flow (CLAUDE.md #9).

Three steps, each an explicit user action — nothing here ever writes to a
TrackFile without a human reviewing the exact before/after values first
("never blindly overwrite user-approved metadata"):

1. search  — query MusicBrainz for candidate recordings.
2. preview — fetch full detail for one chosen candidate, show a diff.
3. apply   — write the previewed fields, only after that same diff was
             re-shown in the apply POST (so the applied fields always
             match what the user actually saw).

Staff-only: this rewrites shared library metadata, not a user's own data.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.library.models import TrackFile
from apps.musicbrainz.services import MusicBrainzClient, MusicBrainzError

# Fields a MusicBrainz recording can propose changes for. Deliberately
# excludes anything filesystem-related (path, filename, format) — this
# flow only ever touches descriptive metadata.
_EDITABLE_FIELDS = ["title", "artist", "album"]


@staff_member_required
def review(request, pk):
    track_file = get_object_or_404(TrackFile, pk=pk)
    candidates = None

    if request.method == "POST" and request.POST.get("action") == "search":
        client = MusicBrainzClient()
        try:
            candidates = client.search_recordings(
                title=request.POST.get("title", track_file.title),
                artist=request.POST.get("artist", track_file.artist),
                album=request.POST.get("album", track_file.album),
            )
        except MusicBrainzError as exc:
            messages.error(request, _("MusicBrainz search failed: %(error)s") % {"error": exc})

    return render(request, "musicbrainz/review.html", {
        "track_file": track_file,
        "candidates": candidates,
        "search_title": request.POST.get("title", track_file.title),
        "search_artist": request.POST.get("artist", track_file.artist),
        "search_album": request.POST.get("album", track_file.album),
    })


@staff_member_required
def preview(request, pk, mbid):
    track_file = get_object_or_404(TrackFile, pk=pk)
    client = MusicBrainzClient()
    try:
        recording = client.get_recording(mbid)
    except MusicBrainzError as exc:
        messages.error(request, _("Could not fetch MusicBrainz recording: %(error)s") % {"error": exc})
        return redirect(reverse("musicbrainz:review", args=[track_file.pk]))

    proposed = {
        "title": recording.title or track_file.title,
        "artist": recording.artist or track_file.artist,
        "album": recording.release or track_file.album,
    }
    diff = {
        field: {"current": getattr(track_file, field), "proposed": proposed[field]}
        for field in _EDITABLE_FIELDS
        if getattr(track_file, field) != proposed[field]
    }

    return render(request, "musicbrainz/preview.html", {
        "track_file": track_file, "recording": recording, "diff": diff, "proposed": proposed,
    })


@staff_member_required
def apply(request, pk, mbid):
    if request.method != "POST":
        return redirect(reverse("musicbrainz:preview", args=[pk, mbid]))

    track_file = get_object_or_404(TrackFile, pk=pk)
    client = MusicBrainzClient()
    try:
        recording = client.get_recording(mbid)
    except MusicBrainzError as exc:
        messages.error(request, _("Could not fetch MusicBrainz recording: %(error)s") % {"error": exc})
        return redirect(reverse("musicbrainz:review", args=[track_file.pk]))

    if recording.title:
        track_file.title = recording.title
    if recording.artist:
        track_file.artist = recording.artist
    if recording.release:
        track_file.album = recording.release
    track_file.save(update_fields=["title", "artist", "album", "updated_at"])
    recording.matched_track_file = track_file
    recording.save(update_fields=["matched_track_file"])

    messages.success(request, _("Metadata updated from MusicBrainz."))
    return redirect(reverse("admin:library_trackfile_change", args=[track_file.pk]))
