# Changelog

## 1.0.0

First stable release.

### Added
- URL submission (single, multiple, and playlist/album), async resolution and download via
  Celery, with live HTMX-polled progress, retry, and cancel.
- SHA-256 exact-duplicate detection, normalized-metadata "probable duplicate" detection, and
  source-identifier duplicate detection (the same track is never re-downloaded just because it
  shows up in a different playlist later).
- Automatic replacement of a probable duplicate with a better version (bigger file, or sourced
  from YouTube Music specifically), admin-configurable.
- MusicBrainz integration: manual search → preview → apply metadata review, automatic
  high-confidence enrichment right after each download, and a slow background sweep (only while
  no batch is active) for tracks that were never checked.
- A searchable `/library/` page with in-browser playback, available to any logged-in user.
- Filesystem library scan (quick/full sync) for manually-added files.
- Admin-managed temporary shared import links: token-hashed, rate-limited, auto-expiring,
  usable without an account.
- PWA support: installable, offline shell, generated icons.
- Light/dark/automatic theming and English + Finnish UI, fully translated.
- SMTP batch-completed/failed notifications (never per-track), admin "send test email".
- Scheduled cleanup for abandoned items/batches, orphaned temp directories, and expired shared
  links.
- Toast-style notifications that dismiss themselves instead of sitting in the page.
- Health (`/health/`) and readiness (`/ready/`) endpoints, Docker healthchecks throughout.

### Removed
- Navidrome integration — removed by request; it was never configured in practice and added
  surface area with no corresponding use. See the amendment note at the top of `CLAUDE.md` if
  it's ever wanted back.

### Fixed
- Restart-safety: a worker container restarted mid-download now resumes automatically, and much
  faster than Celery's own defaults would (`CELERY_VISIBILITY_TIMEOUT_SECONDS`); scheduled
  cleanup now checks every 15 minutes instead of 6 hours, so any item that still falls through
  gets caught and made retryable promptly instead of silently sitting for hours.
- Resolving a large playlist has its own timeout, sized for how long that legitimately takes,
  separate from the much shorter per-track download timeout.
- Corrected duplicate-policy/message-level translations and Bootstrap color classes that
  previously didn't map to real Bootstrap contextual colors.

See `docs/TROUBLESHOOTING.md` for the reasoning and reproduction behind the fixes above, and
`docs/FEATURES.md` for the full, current feature list.
