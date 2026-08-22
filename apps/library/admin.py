from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.library.models import DuplicateMatch, TrackFile


@admin.register(TrackFile)
class TrackFileAdmin(admin.ModelAdmin):
    list_display = ("filename", "artist", "title", "album", "sha256", "size", "created_at")
    search_fields = ("filename", "artist", "title", "album", "sha256")
    readonly_fields = ("sha256", "created_at", "updated_at", "musicbrainz_link")

    def musicbrainz_link(self, obj):
        if not obj.pk:
            return "—"
        url = reverse("musicbrainz:review", args=[obj.pk])
        return format_html('<a class="button" href="{}">Review metadata against MusicBrainz</a>', url)

    musicbrainz_link.short_description = "MusicBrainz"


@admin.register(DuplicateMatch)
class DuplicateMatchAdmin(admin.ModelAdmin):
    list_display = ("original", "duplicate_of", "match_type", "confidence", "resolution", "created_at")
    list_filter = ("match_type", "resolution")
