from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.defaultfilters import filesizeformat
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.audit import log_event
from apps.library.models import TrackFile
from apps.library.services import find_exact_duplicate_groups, remove_exact_duplicates
from apps.library.streaming import build_cover_response, build_stream_response
from apps.library.tasks import scan_library_task

_PAGE_SIZE = 50

# Newest download first — matches the library page's own ordering below.
# Prev/next always walks this fixed ordering, not whatever filter/search
# happened to be active in the page the user clicked play from — that
# state may no longer exist by the time playback reaches this track (the
# player persists across in-app navigation, see static/js/player.js), so
# a stable, library-wide order is the only one guaranteed to still make
# sense. `-id` is an explicit final tiebreaker so the order is fully
# deterministic even when several tracks share the same created_at
# (e.g. a batch of tracks finalized in the same second).
_LIBRARY_ORDERING = ["-created_at", "-id"]


@login_required
def track_list(request):
    """Browse the shared music library (CLAUDE.md #6/#7).

    Read-only for any logged-in user — the library itself isn't
    per-user, unlike DownloadBatch/DownloadItem. Flags exact duplicates
    (same SHA-256) so users can see what duplicate detection already
    caught without needing admin access.
    """
    query = request.GET.get("q", "").strip()

    tracks = TrackFile.objects.filter(removed_at__isnull=True).order_by(*_LIBRARY_ORDERING)
    if query:
        tracks = tracks.filter(
            Q(title__icontains=query) | Q(artist__icontains=query) | Q(album__icontains=query)
        )

    # Exact-duplicate hashes present more than once in the (filtered)
    # result set, computed once up front rather than per row. order_by()
    # is cleared first: annotate() after an order_by() on unrelated
    # fields would otherwise fold those fields into GROUP BY too and
    # fragment the count.
    duplicate_hashes = set(
        tracks.order_by()
        .values("sha256")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("sha256", flat=True)
    )

    paginator = Paginator(tracks, _PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "library/track_list.html", {
        "page": page, "query": query, "duplicate_hashes": duplicate_hashes,
    })


@login_required
def remove_duplicates(request):
    """Staff-only preview-then-confirm page for bulk-removing
    exact-duplicate library files (CLAUDE.md #7/#17): GET shows exactly
    which files would be kept vs. permanently deleted; only a POST to
    the same URL actually removes anything. Never touches "probable"
    (metadata-only, possibly-different-recording) duplicates — see
    apps.library.services.find_exact_duplicate_groups().

    Deleting real audio files is exactly the kind of hard-to-reverse,
    disk-space-reclaiming action that shouldn't be reachable by any
    logged-in library viewer, so this is staff-only unlike the read-only
    library views above.
    """
    if not request.user.is_staff:
        return HttpResponseForbidden()

    if request.method == "POST":
        removed, reclaimed_bytes = remove_exact_duplicates()
        if removed:
            log_event(
                "duplicates_removed", request=request,
                detail={"files_removed": removed, "bytes_reclaimed": reclaimed_bytes},
            )
            messages.success(request, _("Removed %(count)d duplicate file(s).") % {"count": removed})
        else:
            messages.info(request, _("No duplicate files to remove."))
        return redirect(reverse("library:list"))

    groups = find_exact_duplicate_groups()
    total_bytes = sum(track.size for group in groups for track in group[1:])
    return render(request, "library/remove_duplicates.html", {
        "groups": groups,
        "total_to_remove": sum(len(group) - 1 for group in groups),
        "total_bytes_display": filesizeformat(total_bytes),
    })


@login_required
@require_POST
def library_scan_quick(request):
    """Navbar "Quick sync" (staff-only): queue a scan that only adds
    files not already in the database — see scan_music_library()."""
    if not request.user.is_staff:
        return HttpResponseForbidden()
    scan_library_task.delay(full=False)
    log_event("library_scan_triggered", request=request, detail={"mode": "quick"})
    messages.success(request, _("Quick library scan started in the background."))
    return redirect(reverse("library:list"))


@login_required
@require_POST
def library_scan_full(request):
    """Navbar "Full sync" (staff-only): queue a scan that also
    soft-deletes rows for files no longer on disk — see
    scan_music_library(full=True)."""
    if not request.user.is_staff:
        return HttpResponseForbidden()
    scan_library_task.delay(full=True)
    log_event("library_scan_triggered", request=request, detail={"mode": "full"})
    messages.success(request, _("Full library scan started in the background."))
    return redirect(reverse("library:list"))


@login_required
def track_stream(request, pk):
    """Stream one library track's audio for in-browser playback.

    Login is required (same as track_list) but the library isn't
    per-user, so any authenticated user may stream any track. See
    apps.library.streaming.build_stream_response for the actual response.
    """
    track = get_object_or_404(TrackFile, pk=pk, removed_at__isnull=True)
    return build_stream_response(track)


@login_required
def track_cover(request, pk):
    """Serve a track's embedded cover image — see
    apps.library.streaming.build_cover_response."""
    track = get_object_or_404(TrackFile, pk=pk, removed_at__isnull=True)
    return build_cover_response(track)


@login_required
def track_neighbors(request, pk):
    """Return the previous/next track (by the library's standard order).

    Used by the player bar's prev/next buttons (static/js/player.js) to
    keep going without a page load. Returns JSON:
    {"prev": {...} | null, "next": {...} | null}.
    """
    get_object_or_404(TrackFile, pk=pk, removed_at__isnull=True)

    ids = list(
        TrackFile.objects.filter(removed_at__isnull=True)
        .order_by(*_LIBRARY_ORDERING)
        .values_list("id", flat=True)
    )
    try:
        index = ids.index(int(pk))
    except ValueError:
        index = -1

    def serialize(track_id):
        if track_id is None:
            return None
        track = TrackFile.objects.get(pk=track_id)
        return {
            "id": track.id,
            "url": reverse("library:stream", args=[track.id]),
            "cover_url": reverse("library:cover", args=[track.id]),
            "title": track.title or track.filename,
            "artist": track.artist or "",
        }

    prev_id = ids[index - 1] if index > 0 else None
    next_id = ids[index + 1] if 0 <= index < len(ids) - 1 else None
    return JsonResponse({"prev": serialize(prev_id), "next": serialize(next_id)})
