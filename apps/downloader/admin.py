from django.contrib import admin

from apps.downloader.models import DownloadBatch, DownloadItem


class DownloadItemInline(admin.TabularInline):
    model = DownloadItem
    extra = 0
    fields = ("title", "artist", "status", "duplicate_status", "progress", "retry_count")
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(DownloadBatch)
class DownloadBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "created_by", "status", "total_items", "completed_items", "failed_items", "created_at")
    list_filter = ("status", "source_type")
    search_fields = ("title", "created_by__username")
    inlines = [DownloadItemInline]
    readonly_fields = ("created_at", "updated_at")


@admin.register(DownloadItem)
class DownloadItemAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "artist", "batch", "status", "duplicate_status", "retry_count")
    list_filter = ("status", "duplicate_status")
    search_fields = ("title", "artist", "source_identifier")
    readonly_fields = (
        "created_at", "started_at", "completed_at", "cancelled_at",
        "last_command", "last_output",
    )
