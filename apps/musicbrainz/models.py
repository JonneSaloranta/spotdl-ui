from django.db import models


class MusicBrainzRecording(models.Model):
    """Cached MusicBrainz recording lookup result (CLAUDE.md #9).

    Caching avoids repeatedly requesting the same data and helps respect
    MusicBrainz's rate limits.
    """

    mbid = models.CharField(max_length=36, unique=True, help_text="MusicBrainz recording MBID")
    title = models.CharField(max_length=255, blank=True)
    artist = models.CharField(max_length=255, blank=True)
    artist_mbid = models.CharField(max_length=36, blank=True)
    release = models.CharField(max_length=255, blank=True)
    release_mbid = models.CharField(max_length=36, blank=True)
    length_ms = models.PositiveIntegerField(null=True, blank=True)
    isrcs = models.JSONField(default=list, blank=True)
    raw_response = models.JSONField(default=dict, blank=True)

    # A track file this recording has been matched/applied to, once a user
    # has reviewed and approved the metadata (CLAUDE.md #9: never blindly
    # overwrite approved metadata).
    matched_track_file = models.ForeignKey(
        "library.TrackFile", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="musicbrainz_matches",
    )

    fetched_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["artist", "title"])]

    def __str__(self):
        return f"{self.artist} - {self.title} ({self.mbid})"
