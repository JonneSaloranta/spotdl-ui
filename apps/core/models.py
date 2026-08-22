from django.core.cache import cache
from django.db import models
from django.utils.translation import gettext_lazy as _

SITE_SETTINGS_CACHE_KEY = "core:site_settings:singleton"

# spotDL's own --audio choices (`spotdl --help`) — kept as a single shared
# list so the primary-provider dropdown and the fallback checkboxes can
# never drift out of sync with each other or with what spotDL will
# actually accept.
AUDIO_PROVIDER_CHOICES = [
    ("youtube", "YouTube"),
    ("youtube-music", "YouTube Music"),
    ("soundcloud", "SoundCloud"),
    ("bandcamp", "Bandcamp"),
    ("piped", "Piped"),
]


class SiteSettings(models.Model):
    """Administrator-editable application settings.

    This is a singleton table (always primary key 1) so that operators can
    change site-wide, non-secret behaviour from the Django admin — or the
    simplified /settings/ page — without a deployment. Secrets (API keys,
    SMTP passwords, database credentials) never live here; they stay in
    environment variables per CLAUDE.md #15. The spotDL audio-provider
    tiering below is a deliberate exception to "prefer env vars for spotDL
    config": it's something admins were asking to change often enough
    (YouTube's anti-bot blocking varies by deployment) that requiring an
    env var edit + container restart for it was real friction — see
    apps.downloader.tasks.pick_audio_providers(), which reads these
    fields, not the SPOTDL_PRIMARY_AUDIO_PROVIDER-family env vars (those
    remain only as a lower-level default for direct/test use of the
    spotDL service layer without a full SiteSettings row).
    """

    site_name = models.CharField(max_length=100, default="spotDL Web UI")
    maintenance_mode = models.BooleanField(
        default=False,
        help_text="When enabled, non-staff users see a maintenance page instead of the app.",
    )
    maintenance_message = models.TextField(blank=True)

    default_download_format = models.CharField(max_length=10, default="mp3")
    default_duplicate_policy = models.CharField(
        max_length=20,
        choices=[
            ("skip", _("Skip duplicates")),
            ("keep_both", _("Keep both copies")),
            ("ask", _("Ask the user")),
        ],
        default="ask",
        help_text=_(
            "What to do when a newly downloaded track's audio is byte-for-byte identical to "
            "one already in the library (CLAUDE.md #7). “Ask the user” currently "
            "behaves like “Keep both” — the item is flagged as a duplicate either way, "
            "just never silently discarded or overwritten (CLAUDE.md #21)."
        ),
    )
    max_urls_per_batch = models.PositiveIntegerField(default=25)
    auto_replace_probable_duplicates = models.BooleanField(
        default=True,
        help_text=_(
            "When a newly downloaded track has the same artist/title as one already in the "
            "library but different audio (a “probable” duplicate — CLAUDE.md #7), automatically "
            "delete the older file and keep the new one when the new file is bigger, or was "
            "sourced from YouTube Music specifically. Plain YouTube search results are excluded "
            "from that second condition because they more often surface a live performance for "
            "the same title/artist — a genuinely different recording, not just a lower-quality "
            "copy of the studio one — so auto-replacing on that basis would risk losing it. "
            "Files this doesn't replace are kept as separate tracks, same as today."
        ),
    )

    musicbrainz_enabled = models.BooleanField(
        default=True,
        help_text=_(
            "Look up MusicBrainz automatically right after each track finishes downloading, "
            "filling in its MusicBrainz ID and any missing album title from a high-confidence "
            "match, and in the slow background sweep below for tracks never checked. Never "
            "overwrites title/artist/album that are already populated — those can only be "
            "changed through the manual per-track review page (“Review metadata against "
            "MusicBrainz”), which is unaffected by this setting."
        ),
    )
    musicbrainz_sweep_batch_size = models.PositiveIntegerField(
        default=5,
        help_text=_(
            "How many never-checked tracks the background MusicBrainz sweep looks up each time "
            "it runs (apps.musicbrainz.tasks.musicbrainz_sweep_task), instead of doing them all "
            "at once — MusicBrainz itself limits every deployment to about one request per "
            "second regardless, so a smaller number just means each sweep run finishes sooner "
            "and yields the shared rate limit back. The sweep only ever runs when no download "
            "batch is currently active, so it never competes with real downloads for a worker."
        ),
    )

    # Try this provider for the first `primary_provider_attempts` attempts
    # on a track, then switch to `fallback_audio_providers` for any
    # attempts after that. Blank primary means "let spotDL use its own
    # default" for those attempts. See apps.downloader.tasks.pick_audio_providers().
    primary_audio_provider = models.CharField(
        max_length=20, choices=AUDIO_PROVIDER_CHOICES, default="youtube", blank=True,
    )
    primary_provider_attempts = models.PositiveSmallIntegerField(default=3)
    # Stored as a comma-separated string rather than a Postgres ArrayField
    # so this stays portable and the admin form can use a plain
    # multi-select without extra field-type plumbing; always accessed via
    # fallback_audio_providers_list()/_set() below rather than directly.
    fallback_audio_providers = models.CharField(max_length=200, blank=True, default="soundcloud")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return self.site_name

    def fallback_audio_providers_list(self) -> list[str]:
        return [p.strip() for p in self.fallback_audio_providers.split(",") if p.strip()]

    def set_fallback_audio_providers_list(self, providers: list[str]) -> None:
        self.fallback_audio_providers = ",".join(providers)

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(SITE_SETTINGS_CACHE_KEY)

    @classmethod
    def load(cls) -> "SiteSettings":
        """Return the singleton instance, using a short-lived cache.

        Falls back to constructing an unsaved default instance if the
        table has no row yet (e.g. before the first migration data is
        seeded), so callers never need to special-case a missing settings
        row.
        """
        cached = cache.get(SITE_SETTINGS_CACHE_KEY)
        if cached is not None:
            return cached
        obj, _created = cls.objects.get_or_create(pk=1)
        cache.set(SITE_SETTINGS_CACHE_KEY, obj, timeout=60)
        return obj


class AuditLog(models.Model):
    """Security and administrative event log (CLAUDE.md #6, docs/DATA_MODEL.md)."""

    class Action(models.TextChoices):
        LOGIN = "login", "Login"
        LOGIN_FAILED = "login_failed", "Login failed"
        LOGOUT = "logout", "Logout"
        BATCH_CREATED = "batch_created", "Download batch created"
        BATCH_CANCELLED = "batch_cancelled", "Download batch cancelled"
        BATCH_RETRIED = "batch_retried", "Download batch items retried"
        ITEM_RETRIED = "item_retried", "Download item retried"
        SHARED_LINK_CREATED = "shared_link_created", "Shared link created"
        SHARED_LINK_DISABLED = "shared_link_disabled", "Shared link disabled"
        SHARED_LINK_USED = "shared_link_used", "Shared link used"
        SETTINGS_CHANGED = "settings_changed", "Settings changed"
        DUPLICATES_REMOVED = "duplicates_removed", "Duplicate library files removed"
        LIBRARY_SCAN_TRIGGERED = "library_scan_triggered", "Library scan triggered"
        ADMIN_ACTION = "admin_action", "Administrative action"

    actor = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    action = models.CharField(max_length=32, choices=Action.choices)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    target_repr = models.CharField(max_length=255, blank=True)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} @ {self.created_at:%Y-%m-%d %H:%M}"
