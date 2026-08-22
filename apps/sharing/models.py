import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class SharedImportLink(models.Model):
    """An admin-created invite that gives someone without an account the
    right to download music through this app for a limited time/number of
    uses (CLAUDE.md #10) — they get their own simple submission form and a
    status/downloaded-music view (apps.sharing.views), scoped to only what
    was downloaded through *their* link; they never see anyone else's
    batches, downloads, or the full library.

    Only a SHA-256 hash of the token is stored (docs/SECURITY.md: "Use
    cryptographically secure random tokens" / "Prefer storing a hash of the
    token"), so a database read alone cannot be used to impersonate a link.
    The raw token is only ever available once, at creation time.
    """

    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    label = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shared_links"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    enabled = models.BooleanField(default=True)
    maximum_uses = models.PositiveIntegerField(default=1)
    uses = models.PositiveIntegerField(default=0)
    max_items_per_submission = models.PositiveIntegerField(default=25)
    allowed_actions = models.JSONField(
        default=list, blank=True,
        help_text="e.g. ['submit_urls']. Reserved for future fine-grained scopes.",
    )

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]

    def __str__(self):
        return self.label or f"Shared link #{self.pk}"

    @classmethod
    def generate(cls, *, created_by, ttl_hours: int, maximum_uses: int, label: str = "",
                 max_items_per_submission: int = 25) -> tuple["SharedImportLink", str]:
        """Create a new link and return (instance, raw_token).

        The raw token is returned exactly once — callers must show/send it
        to the admin immediately, since it cannot be recovered afterwards.
        """
        raw_token = secrets.token_urlsafe(32)
        instance = cls.objects.create(
            token_hash=_hash_token(raw_token),
            created_by=created_by,
            expires_at=timezone.now() + timezone.timedelta(hours=ttl_hours),
            maximum_uses=maximum_uses,
            label=label,
            max_items_per_submission=max_items_per_submission,
        )
        return instance, raw_token

    @classmethod
    def get_valid_by_token(cls, raw_token: str) -> "SharedImportLink | None":
        """Look up a link by raw token, returning None unless it is
        currently usable for a *new submission* — enabled, unexpired, and
        with remaining uses. See get_viewable_by_token() for the looser
        check used to keep showing past results after uses run out."""
        link = cls._get_by_token(raw_token)
        return link if link is not None and link.is_valid else None

    @classmethod
    def get_viewable_by_token(cls, raw_token: str) -> "SharedImportLink | None":
        """Look up a link by raw token for *viewing* status/downloads —
        enabled and unexpired, but unlike get_valid_by_token() this does
        not require remaining uses: a guest should still be able to see
        what they already downloaded (and stream it) after using up their
        submission allowance, right up until the link actually expires or
        an admin disables it."""
        link = cls._get_by_token(raw_token)
        return link if link is not None and link.is_viewable else None

    @classmethod
    def _get_by_token(cls, raw_token: str) -> "SharedImportLink | None":
        if not raw_token:
            return None
        try:
            return cls.objects.get(token_hash=_hash_token(raw_token))
        except cls.DoesNotExist:
            return None

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_exhausted(self) -> bool:
        return self.uses >= self.maximum_uses

    @property
    def is_viewable(self) -> bool:
        return self.enabled and not self.is_expired

    @property
    def is_valid(self) -> bool:
        return self.is_viewable and not self.is_exhausted

    def register_use(self) -> None:
        """Atomically consume one use. Call inside a transaction with
        select_for_update to avoid a race that exceeds maximum_uses under
        concurrent submissions."""
        self.uses = models.F("uses") + 1
        self.save(update_fields=["uses"])
        self.refresh_from_db(fields=["uses"])
