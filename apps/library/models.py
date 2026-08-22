from django.db import models


class TrackFile(models.Model):
    """A music file that has actually been written to the library.

    Created once a download is finalized (hashed, tagged, moved into
    MUSIC_ROOT). `sha256` is the canonical identity used for exact-duplicate
    detection (CLAUDE.md #7); nothing here trusts the filename alone.
    """

    path = models.CharField(
        max_length=1024, unique=True,
        help_text="Path relative to MUSIC_ROOT. Never an absolute filesystem path.",
    )
    filename = models.CharField(max_length=255)
    size = models.BigIntegerField(help_text="Bytes")
    sha256 = models.CharField(max_length=64, db_index=True)
    duration = models.FloatField(null=True, blank=True, help_text="Seconds")
    bitrate = models.PositiveIntegerField(null=True, blank=True, help_text="bits/sec")
    format = models.CharField(max_length=16, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    # Which spotDL audio provider actually supplied this file (e.g.
    # "youtube", "youtube-music", "soundcloud") — copied from
    # DownloadItem.source_provider at finalization time. Blank for a file
    # this app never downloaded itself (a manually placed file picked up
    # by a library scan — apps.library.services.scan_music_library()).
    source_provider = models.CharField(max_length=20, blank=True)

    title = models.CharField(max_length=255, blank=True)
    artist = models.CharField(max_length=255, blank=True)
    album = models.CharField(max_length=255, blank=True)
    album_artist = models.CharField(max_length=255, blank=True)
    track_number = models.PositiveIntegerField(null=True, blank=True)
    disc_number = models.PositiveIntegerField(null=True, blank=True)
    year = models.PositiveIntegerField(null=True, blank=True)

    # Normalized (lowercased, punctuation-stripped) copies used for fast
    # probable-duplicate lookups without re-normalizing on every query.
    normalized_artist = models.CharField(max_length=255, blank=True, db_index=True)
    normalized_title = models.CharField(max_length=255, blank=True, db_index=True)

    # Set once a high-confidence MusicBrainz match has been accepted — either
    # automatically (apps.musicbrainz.services.auto_enrich_track(), right
    # after download or via the background sweep) or through the manual
    # per-track review/apply page. Blank means no confident match was ever
    # found, which is different from musicbrainz_checked_at being unset
    # (never looked up at all) — see that field below.
    musicbrainz_id = models.CharField(max_length=36, blank=True, db_index=True)
    # When this track was last looked up against MusicBrainz (match or not),
    # so the background sweep (apps.musicbrainz.tasks.musicbrainz_sweep_task)
    # can find tracks that have never been checked instead of re-querying
    # every track on every run — CLAUDE.md #9's "no excessive requests".
    musicbrainz_checked_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_scanned_at = models.DateTimeField(null=True, blank=True)

    # Soft deletion: never destructively remove a TrackFile row just
    # because a source download disappears (CLAUDE.md #17). The file on
    # disk is the source of truth; this only marks the record stale.
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["sha256"]),
            models.Index(fields=["normalized_artist", "normalized_title"]),
        ]

    def __str__(self):
        return self.path


class DuplicateMatch(models.Model):
    """A recorded duplicate relationship between two TrackFiles.

    Kept as an explicit audit trail rather than only a point-in-time UI
    decision, per CLAUDE.md #17.
    """

    class MatchType(models.TextChoices):
        EXACT_HASH = "exact_hash", "Exact SHA-256 match"
        METADATA = "metadata", "Metadata match"
        SOURCE_IDENTIFIER = "source_identifier", "Same source identifier"
        DURATION = "duration", "Duration similarity"

    class Resolution(models.TextChoices):
        SKIPPED = "skipped", "Skipped"
        KEPT_BOTH = "kept_both", "Kept both"
        REPLACED = "replaced", "Replaced with a better version"
        PENDING = "pending", "Awaiting decision"

    original = models.ForeignKey(TrackFile, on_delete=models.CASCADE, related_name="+")
    duplicate_of = models.ForeignKey(TrackFile, on_delete=models.CASCADE, related_name="+")
    match_type = models.CharField(max_length=20, choices=MatchType.choices)
    confidence = models.FloatField(default=1.0, help_text="0.0-1.0")
    resolution = models.CharField(max_length=12, choices=Resolution.choices, default=Resolution.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["match_type"])]
