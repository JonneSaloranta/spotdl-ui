from django.db import transaction
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from apps.core.audit import client_ip, log_event
from apps.core.ratelimit import is_rate_limited
from apps.downloader.models import BatchStatus, DownloadBatch, DownloadItem
from apps.downloader.tasks import queue_batch_resolve
from apps.library.streaming import build_cover_response, build_stream_response
from apps.sharing.forms import SharedSubmitForm
from apps.sharing.models import SharedImportLink

# Deliberately coarse: shared links are meant for occasional use by people
# without accounts, not sustained traffic (CLAUDE.md #10/#20).
_SUBMIT_LIMIT = 5
_SUBMIT_WINDOW_SECONDS = 300
_VIEW_LIMIT = 30
_VIEW_WINDOW_SECONDS = 300

_RECENT_TRACKS_LIMIT = 20  # "20 viimeisintä ladattua musiikkia"
_STATUS_ITEMS_LIMIT = 50


def _link_context(link: SharedImportLink, token: str) -> dict:
    """Status/downloads shown to a shared-link guest — scoped to only
    what came through *this* link (CLAUDE.md #10: "never expose other
    users' downloads"), never the full site library or other batches.
    Shared by the full page and its polling partial (_status.html), both
    of which also need the raw `token` itself to build the track
    stream/cover/poll URLs (sharing:status, sharing:track_stream, ...).
    """
    items = (
        DownloadItem.objects.filter(batch__shared_link=link)
        .order_by("-created_at")[:_STATUS_ITEMS_LIMIT]
    )
    tracks = [
        item.result_file for item in
        DownloadItem.objects.filter(batch__shared_link=link, result_file__isnull=False)
        .select_related("result_file")
        .order_by("-completed_at")[:_RECENT_TRACKS_LIMIT]
    ]
    return {"items": items, "tracks": tracks, "token": token}


@require_http_methods(["GET", "POST"])
def shared_submit(request, token: str):
    """Public (no-account) invite page behind a shared link (CLAUDE.md #10).

    A guest gets their own URL submission form (like an authenticated
    user's, just capped by the link's own limits) plus a simple view of
    what's been downloaded through this specific link — never anyone
    else's downloads, never the full library, never an admin/staff page.

    Never reveals whether a token is invalid vs. expired vs. disabled in
    detail beyond a generic message. An exhausted link (no submissions
    left) is different from a dead one, though: it keeps showing past
    results — see SharedImportLink.is_viewable vs. is_valid — right up
    until it actually expires or an admin disables it.
    """
    ip = client_ip(request) or "unknown"

    if is_rate_limited(f"shared:view:{ip}", limit=_VIEW_LIMIT, window_seconds=_VIEW_WINDOW_SECONDS):
        return HttpResponseForbidden(_("Too many requests. Please try again later."))

    link = SharedImportLink.get_viewable_by_token(token)
    if link is None:
        return render(request, "sharing/invalid.html", status=404)

    if request.method == "GET":
        form = SharedSubmitForm(max_urls=link.max_items_per_submission) if link.is_valid else None
        return render(request, "sharing/submit.html", {"link": link, "form": form, **_link_context(link, token)})

    if not link.is_valid:
        # Exhausted (or disabled/expired right between page load and this
        # POST) — still show the page, just without a form to submit
        # through, rather than a generic "unavailable" 404 that would
        # read as the whole link having died.
        return render(request, "sharing/submit.html", {"link": link, "form": None, **_link_context(link, token)})

    if is_rate_limited(f"shared:submit:{ip}:{link.pk}", limit=_SUBMIT_LIMIT, window_seconds=_SUBMIT_WINDOW_SECONDS):
        return HttpResponseForbidden(_("Too many submissions. Please try again later."))

    form = SharedSubmitForm(request.POST, max_urls=link.max_items_per_submission)
    if not form.is_valid():
        return render(request, "sharing/submit.html", {"link": link, "form": form, **_link_context(link, token)})

    with transaction.atomic():
        # Re-check validity under a row lock so two near-simultaneous
        # submissions cannot both slip past an exhausted use count.
        locked_link = SharedImportLink.objects.select_for_update().get(pk=link.pk)
        if not locked_link.is_valid:
            return render(request, "sharing/submit.html", {
                "link": locked_link, "form": None, **_link_context(locked_link, token),
            })

        validated_urls = form.cleaned_data["urls"]
        batch = DownloadBatch.objects.create(
            created_by=None,
            shared_link=locked_link,
            source_urls=[u.normalized for u in validated_urls],
            status=BatchStatus.PENDING,
        )
        locked_link.register_use()

    log_event(
        "shared_link_used", request=request, target=locked_link,
        detail={"url_count": len(validated_urls), "batch_id": batch.id},
    )
    queue_batch_resolve(batch)

    return render(request, "sharing/submit.html", {
        "link": locked_link,
        "form": SharedSubmitForm(max_urls=locked_link.max_items_per_submission) if locked_link.is_valid else None,
        "queued_count": len(validated_urls),
        **_link_context(locked_link, token),
    })


@require_http_methods(["GET"])
def shared_status_partial(request, token: str):
    """Polling target (CLAUDE.md #12) for the status/downloads section."""
    link = SharedImportLink.get_viewable_by_token(token)
    if link is None:
        raise Http404
    return render(request, "sharing/_status.html", {"link": link, **_link_context(link, token)})


def _get_own_item_or_404(token: str, track_id: int) -> DownloadItem:
    """A track is only streamable through a link if it actually resulted
    from a download made *through that link* — an id alone is never
    enough, closing off the obvious "guess another track's id" probe."""
    link = SharedImportLink.get_viewable_by_token(token)
    if link is None:
        raise Http404
    return get_object_or_404(
        DownloadItem.objects.select_related("result_file"),
        batch__shared_link=link, result_file_id=track_id,
    )


@require_http_methods(["GET"])
def shared_track_stream(request, token: str, track_id: int):
    """Stream a track downloaded through this link — see
    apps.library.streaming.build_stream_response for the response itself."""
    item = _get_own_item_or_404(token, track_id)
    return build_stream_response(item.result_file)


@require_http_methods(["GET"])
def shared_track_cover(request, token: str, track_id: int):
    """Cover art for a track downloaded through this link — see
    apps.library.streaming.build_cover_response for the response itself."""
    item = _get_own_item_or_404(token, track_id)
    return build_cover_response(item.result_file)
