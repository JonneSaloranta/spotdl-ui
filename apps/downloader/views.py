from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.audit import log_event
from apps.core.models import SiteSettings
from apps.downloader.forms import BatchSubmitForm
from apps.downloader.models import BatchStatus, DownloadBatch, DownloadItem, ItemStatus
from apps.downloader.tasks import download_item_task, queue_batch_resolve


def _own_batch_or_403(request, pk):
    batch = get_object_or_404(DownloadBatch, pk=pk)
    if batch.created_by_id != request.user.id and not request.user.is_staff:
        return None
    return batch


@login_required
@require_POST
def submit_batch(request):
    """Handle the main URL-submission form (CLAUDE.md #5 steps 1-3)."""
    max_urls = SiteSettings.load().max_urls_per_batch
    form = BatchSubmitForm(request.POST, max_urls=max_urls)

    if not form.is_valid():
        for error in form.errors.get("urls", []):
            messages.error(request, error)
        return redirect(reverse("core:home"))

    validated_urls = form.cleaned_data["urls"]
    batch = DownloadBatch.objects.create(
        created_by=request.user,
        source_urls=[u.normalized for u in validated_urls],
        status=BatchStatus.PENDING,
    )
    log_event("batch_created", request=request, target=batch, detail={"url_count": len(validated_urls)})

    queue_batch_resolve(batch)
    messages.success(request, _("Import started. Tracks will appear below as they are found."))
    return redirect(reverse("downloader:batch_detail", args=[batch.id]))


@login_required
def batch_detail(request, pk):
    batch = _own_batch_or_403(request, pk)
    if batch is None:
        return HttpResponseForbidden()
    return render(request, "downloader/batch_detail.html", {"batch": batch})


@login_required
def batch_status_partial(request, pk):
    """HTMX polling endpoint: re-renders the item list fragment (CLAUDE.md #12)."""
    batch = _own_batch_or_403(request, pk)
    if batch is None:
        return HttpResponseForbidden()
    return render(request, "downloader/_batch_items.html", {"batch": batch})


@login_required
@require_POST
def batch_cancel(request, pk):
    batch = _own_batch_or_403(request, pk)
    if batch is None:
        return HttpResponseForbidden()

    cancellable = {ItemStatus.PENDING, ItemStatus.QUEUED, ItemStatus.DOWNLOADING, ItemStatus.PROCESSING}
    now = timezone.now()
    batch.items.filter(status__in=cancellable).update(status=ItemStatus.CANCELLED, cancelled_at=now)
    batch.recompute_status()
    log_event("batch_cancelled", request=request, target=batch)
    messages.info(request, _("Remaining items were cancelled."))
    return redirect(reverse("downloader:batch_detail", args=[batch.id]))


def _requeue_batch_items(batch: DownloadBatch, statuses: set) -> int:
    """Reset every item of `batch` currently in one of `statuses` back to
    PENDING and queue it — the same reset as a single item_retry(), just
    applied to many items in one action. Returns how many were requeued.

    Queries the ids up front and requeues by id rather than iterating
    live model instances: with hundreds of items in a batch (a large
    playlist), this keeps it to one UPDATE plus one .delay() per item
    instead of one UPDATE per item.
    """
    ids = list(batch.items.filter(status__in=statuses).values_list("id", flat=True))
    if not ids:
        return 0
    batch.items.filter(id__in=ids).update(
        status=ItemStatus.PENDING, error_message="", retry_count=0, cancelled_at=None,
    )
    batch.recompute_status()
    for item_id in ids:
        download_item_task.delay(item_id)
    return len(ids)


@login_required
@require_POST
def batch_retry_failed(request, pk):
    """'Lataa epäonnistuneet' — requeue only this batch's failed items."""
    batch = _own_batch_or_403(request, pk)
    if batch is None:
        return HttpResponseForbidden()

    count = _requeue_batch_items(batch, {ItemStatus.FAILED})
    if count:
        log_event("batch_retried", request=request, target=batch, detail={"scope": "failed", "item_count": count})
        messages.success(request, _("Retrying %(count)d failed item(s).") % {"count": count})
    else:
        messages.info(request, _("No failed items to retry."))
    return redirect(reverse("downloader:batch_detail", args=[batch.id]))


@login_required
@require_POST
def batch_retry_all(request, pk):
    """'Lataa kaikki' — requeue every item that hasn't already
    succeeded (failed and cancelled alike), not just the failed ones."""
    batch = _own_batch_or_403(request, pk)
    if batch is None:
        return HttpResponseForbidden()

    count = _requeue_batch_items(batch, {ItemStatus.FAILED, ItemStatus.CANCELLED})
    if count:
        log_event("batch_retried", request=request, target=batch, detail={"scope": "all", "item_count": count})
        messages.success(request, _("Retrying %(count)d item(s).") % {"count": count})
    else:
        messages.info(request, _("Nothing to retry."))
    return redirect(reverse("downloader:batch_detail", args=[batch.id]))


@login_required
@require_POST
def item_retry(request, pk):
    item = get_object_or_404(DownloadItem.objects.select_related("batch"), pk=pk)
    batch = item.batch
    if batch.created_by_id != request.user.id and not request.user.is_staff:
        return HttpResponseForbidden()

    if item.status != ItemStatus.FAILED:
        messages.error(request, _("Only failed items can be retried."))
        return redirect(reverse("downloader:batch_detail", args=[batch.id]))

    item.status = ItemStatus.PENDING
    item.error_message = ""
    item.retry_count = 0
    item.save(update_fields=["status", "error_message", "retry_count"])
    download_item_task.delay(item.id)
    log_event("item_retried", request=request, target=item)
    return redirect(reverse("downloader:batch_detail", args=[batch.id]))


@login_required
@require_POST
def item_cancel(request, pk):
    item = get_object_or_404(DownloadItem.objects.select_related("batch"), pk=pk)
    batch = item.batch
    if batch.created_by_id != request.user.id and not request.user.is_staff:
        return HttpResponseForbidden()

    cancellable = {ItemStatus.PENDING, ItemStatus.QUEUED, ItemStatus.DOWNLOADING, ItemStatus.PROCESSING}
    if item.status in cancellable:
        item.status = ItemStatus.CANCELLED
        item.cancelled_at = timezone.now()
        item.save(update_fields=["status", "cancelled_at"])
        batch.recompute_status()
    return redirect(reverse("downloader:batch_detail", args=[batch.id]))
