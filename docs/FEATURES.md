# Feature checklist

Reflects what's actually implemented and tested as of 1.0.0, not the original planning wishlist.

## Core
- [x] Authentication
- [x] Mobile-first Bootstrap UI
- [x] Multiple URL input
- [x] Playlist input
- [x] Batch imports
- [x] Async downloads (Celery)
- [x] Live progress (HTMX polling)
- [x] Retry (per-item and per-batch)
- [x] Cancel
- [x] Download history
- [x] Persistent PostgreSQL state, restart-safe (CLAUDE.md #16 — see docs/TROUBLESHOOTING.md)

## Duplicate detection
- [x] SHA-256 every completed file
- [x] Exact hash duplicate detection
- [x] Metadata duplicate detection (normalized artist+title)
- [x] Source-identifier duplicate detection (same Spotify track skipped across separate imports,
      before ever downloading again — `apps.library.services.find_existing_download()`)
- [x] Automatic replacement of a "probable" duplicate with a better version (bigger file, or
      sourced from YouTube Music specifically — admin-configurable)
- [x] Safe filename handling
- [x] No silent overwrite
- [x] Manual "remove duplicates" cleanup action (staff-only, preview-then-confirm)

## MusicBrainz
- [x] Search recordings (includes release/album info per result; no separate release search)
- [x] Store MBIDs
- [x] Cache API responses
- [x] Rate limiting (~1 req/s, shared across workers)
- [x] Manual metadata review (search → preview diff → apply, staff-only)
- [x] Automatic enrichment right after each download (high-confidence matches only)
- [x] Background sweep for never-checked tracks (only while no batch is active)

## Library
- [x] Searchable `/library/` page for any logged-in user
- [x] In-browser playback with persistent player bar
- [x] Filesystem scan (quick/full sync) for manually-added files, admin-triggered

## Sharing
- [x] Admin creates shared links
- [x] Configurable expiration
- [x] Configurable usage limits
- [x] Anonymous import page (no account required)
- [x] Rate limiting

## UI
- [x] English
- [x] Finnish
- [x] Language switcher
- [x] Light theme
- [x] Dark theme
- [x] System theme
- [x] PWA (installable, offline shell, generated icons)
- [x] Responsive design (phone/tablet/desktop)
- [x] Accessibility basics (labels, keyboard navigation, semantic HTML)
- [x] Auto-dismissing toast notifications

## Operations
- [x] SMTP
- [x] Email notifications (batch completed/failed, one per batch — never per track)
- [x] Health (`/health/`) and readiness (`/ready/`) endpoints
- [x] Docker healthchecks (db/redis/web/worker/nginx)
- [x] Scheduled cleanup (abandoned items/batches, orphaned temp dirs, expired shared links)
- [x] Backup documentation (database and music library, separately)
- [x] Security hardening (see docs/SECURITY.md)

## Explicitly not included
- Navidrome integration — removed by request; see the amendment note at the top of `CLAUDE.md`.
- A dedicated simplified admin UI beyond Django admin + the `/settings/` page (see
  `docs/ADMIN_GUIDE.md`).
