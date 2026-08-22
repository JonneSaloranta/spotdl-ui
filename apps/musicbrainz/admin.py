from django.contrib import admin

from apps.musicbrainz.models import MusicBrainzRecording


@admin.register(MusicBrainzRecording)
class MusicBrainzRecordingAdmin(admin.ModelAdmin):
    list_display = ("artist", "title", "release", "mbid", "fetched_at")
    search_fields = ("artist", "title", "mbid")
    readonly_fields = ("fetched_at", "updated_at", "raw_response")
