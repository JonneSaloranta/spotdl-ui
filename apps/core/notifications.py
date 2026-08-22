"""Batch-completion email notifications (CLAUDE.md #17).

Sent once per batch, on its transition into a finished status (see
DownloadBatch._notify_finished) — never per individual track, and never
for a shared-link submission (those have no user account/email to notify).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

from apps.downloader.models import BatchStatus

logger = logging.getLogger(__name__)

_FAILURE_STATUSES = {BatchStatus.FAILED, BatchStatus.CANCELLED}


def send_batch_finished_email(batch) -> None:
    user = batch.created_by
    if user is None or not user.email:
        return  # anonymous/shared-link batches have no account to notify

    profile = getattr(user, "profile", None)
    is_failure = batch.status in _FAILURE_STATUSES
    wants_notification = (
        profile is None
        or (profile.notify_on_batch_failed if is_failure else profile.notify_on_batch_complete)
    )
    if not wants_notification:
        return

    context = {"batch": batch, "user": user, "is_failure": is_failure}
    subject = render_to_string("core/email/batch_finished_subject.txt", context).strip()
    body = render_to_string("core/email/batch_finished_body.txt", context)

    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
    except Exception:
        # A notification failure must never take down the task that
        # triggered it (CLAUDE.md #16: background side-effects must never
        # block or fail the work that triggered them).
        logger.exception("Failed to send batch-finished email for batch %s", batch.pk)
