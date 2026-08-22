# Data model

The actual model relationships as built (see each app's `models.py` for the full field list):

```
User
  ├── UserProfile              (1:1 — theme, language, notification preferences)
  ├── DownloadBatch            (created_by, SET_NULL on user deletion)
  │     └── DownloadItem
  │           └── TrackFile    (result_file, SET_NULL — a batch/item's history survives
  │                              even after its file is later removed)
  ├── SharedImportLink         (created_by — admin who created the link)
  └── AuditLog                 (actor, SET_NULL — the log entry outlives the user)

SharedImportLink
  └── DownloadBatch            (shared_link, SET_NULL — a batch survives its link expiring)

TrackFile
  ├── DuplicateMatch           (original / duplicate_of, both CASCADE)
  └── MusicBrainzRecording     (matched_track_file, SET_NULL)
```

`SiteSettings` is a singleton (always `pk=1`), not related to any of the above — see
`apps.core.models.SiteSettings.load()`.

Indexes worth knowing about:
- `TrackFile.sha256`, `TrackFile.normalized_artist`/`normalized_title`,
  `TrackFile.musicbrainz_id`, `TrackFile.musicbrainz_checked_at`
- `DownloadItem.source_identifier`
- `SharedImportLink` stores a hash of its token, never the token itself (CLAUDE.md #10)

Soft deletion is used for `TrackFile` (`removed_at`) — a duplicate cleanup or a library scan
noticing a missing file never deletes the database row, only marks it removed, per CLAUDE.md
#17. `DownloadBatch`/`DownloadItem`/`AuditLog` are never deleted at all except by explicit
scheduled cleanup of long-expired `SharedImportLink` rows.
