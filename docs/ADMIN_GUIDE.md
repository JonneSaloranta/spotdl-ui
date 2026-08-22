# Administrator guide

## Accessing administration

Django admin is at `/admin/`, reachable to any user with `is_staff=True`. There is currently no
separate simplified settings UI beyond what's described below — Django admin is the
administration surface.

Grant staff access:

```bash
docker compose exec web python manage.py shell -c "
from django.contrib.auth.models import User
u = User.objects.get(username='someone')
u.is_staff = True
u.save()
"
```

or through `/admin/auth/user/` once you have at least one staff account (created via
`createsuperuser` during installation).

## Site settings

`/admin/core/sitesettings/` — a singleton (always one row). Controls:

- **Site name** — shown in the navbar, page titles, and the PWA manifest.
- **Maintenance mode** — when on, every non-staff visitor sees a maintenance page (`/health/`
  and `/admin/login/` stay reachable regardless, so you can always turn it back off).
- **Default duplicate policy** — `skip` / `keep_both` / `ask` (currently treated the same as
  `keep_both`; see `apps/library/services.py`). Controls what happens when a newly downloaded
  file is an exact SHA-256 duplicate of one already in the library.
- **Automatically replace probable duplicates with a better version** (on by default) — when a
  newly downloaded track has the same artist/title as one already in the library but different
  audio (a "probable" duplicate, not an exact one), automatically delete the older file and keep
  the new one if the new file is bigger, or came from YouTube Music specifically. Plain YouTube
  is deliberately excluded from the source condition: a plain YouTube search is more likely to
  surface a live performance for the same title/artist — a genuinely different recording, not
  just a lower-quality copy — so replacing on that basis alone would risk losing it. Recorded in
  `/admin/library/duplicatematch/` with `resolution=replaced`. Turn off to always keep both
  copies instead, same as before this existed.
- **Max URLs per batch** — caps how many URLs a logged-in user can submit at once.
- **MusicBrainz enabled** — the real gate for both automatic enrichment right after a download
  and the background sweep below; the manual per-track review page is unaffected by it. On by
  default.
- **MusicBrainz background sweep batch size** (default `5`) — how many never-checked tracks the
  sweep looks up each time it runs. See "MusicBrainz" below.

The "Send test email" button on that page's changelist sends a test message to your own admin
account's email address — use it to verify SMTP after configuring it.

## Users

`/admin/auth/user/` — standard Django user administration. Each user has a `UserProfile`
(theme preference, preferred language, notification toggles), auto-created on first save via a
signal (`apps/accounts/signals.py`), editable inline on the user's admin page.

## Download batches and items

`/admin/downloader/downloadbatch/` and `/admin/downloader/downloaditem/` — read access to every
user's batches (the regular UI at `/` only shows a user their own). Useful for diagnosing a
stuck or failed batch without needing database access. Status/progress/error fields are
read-only there; use the app's own retry/cancel buttons (as that user, or reproduce the action
via `manage.py shell`) rather than editing status directly, since batch aggregates
(`completed_items` etc.) are derived from items via `DownloadBatch.recompute_status()` and
editing a row directly in admin won't recompute them.

## Library

**File layout.** Every downloaded track is stored directly in `MUSIC_ROOT` itself (flat, one
folder) — deliberately not nested under artist/album subfolders, unlike CLAUDE.md #22's suggested
default. Two different tracks that would otherwise sanitize to the same filename are never
overwritten: the second one gets a short hash suffix instead (see
`apps.library.filenames.sanitize_relative_path()` and `_store_new_file()` in
`apps/library/services.py`).

`/library/` (in the regular app, not admin) is a read-only, searchable view of every downloaded
track, available to any logged-in user, with an exact-duplicate badge. `/admin/library/trackfile/`
adds full edit access plus a "Review metadata against MusicBrainz" link per track (see below).
`/admin/library/duplicatematch/` shows the audit trail of every duplicate detected, with a
`resolution` field (`pending` / `skipped` / `kept_both`) you can set manually if you want to
track how a duplicate was actually resolved by a human.

**Skipping already-downloaded sources.** Before a newly-resolved track is ever queued for
download, `resolve_batch_task` checks whether the exact same Spotify source (matched by its
track ID, falling back to its URL) was already successfully downloaded in *any* past import and
that file still exists — if so, the new item is marked as a duplicate immediately, pointing at
the existing file, and no download is attempted at all. This is what lets the same track show up
in several different playlists you import over time without re-downloading it every time. If the
earlier file was since removed (manual duplicate cleanup, replaced by a better version, ...), the
track downloads again normally instead of silently staying missing. See
`apps.library.services.find_existing_download()`.

**Removing duplicate files.** Staff see a "Remove duplicates" button on `/library/`, leading to a
preview-then-confirm page (`/library/duplicates/`) listing exactly which files would be kept
(always the oldest copy) and which would be permanently deleted, with the total disk space that
would be reclaimed. Nothing is deleted until you confirm. Only **exact** duplicates — files whose
audio is byte-for-byte identical (same SHA-256) — are ever eligible; a "probable" duplicate (same
artist/title, different audio — a remaster, a live version, a re-encode) is never touched here,
since it might genuinely be a different recording worth keeping. The removed file's `TrackFile`
row is soft-deleted (`removed_at`, CLAUDE.md #17), not hard-deleted, so `DuplicateMatch` history
stays intact; the underlying file is actually removed from disk, since reclaiming that space is
the entire point of the action. Logged to the audit log as `duplicates_removed`.

**Library scan.** Picks up audio files under `MUSIC_ROOT` this app didn't download itself — a
restore from backup, a file copied in by hand, anything placed there outside the normal pipeline.
Staff get a "Library sync" menu in the navbar (visible on every page) with two actions, both
queued on Celery in the background (`apps.library.tasks.scan_library_task`) rather than blocking
the request:

- **Quick sync** — only adds files not already in the database. Same SHA-256 hashing and
  exact/probable duplicate detection as a normal download, but never deletes anything, even a
  file that turns out to be a duplicate — a passive scan silently removing a file a human just
  placed there would be a nasty surprise, so that stays limited to the explicit actions above.
- **Full sync** — everything quick sync does, plus soft-deletes any `TrackFile` row whose file no
  longer exists on disk, and refreshes `last_scanned_at` on every row it verified is still
  present. A genuine reconciliation against what's actually there, so use it deliberately — not
  automatically, since a `MUSIC_ROOT` that's temporarily unavailable (an unmounted volume, a
  network filesystem hiccup) must never look like every file in it was deleted.

A **quick** sync also runs automatically every time the `web` container starts
(`docker/entrypoint.sh`, queued via `manage.py queue_library_scan`) — never a full sync
unattended, for the same reason. Both are also available from the CLI: `manage.py scan_library`
(add `--full` for a full sync) runs synchronously in the current process rather than on Celery,
useful for scripting or when you want to see the result immediately.

## MusicBrainz

**Manual review.** From any `TrackFile`'s admin change page, the "Review metadata against
MusicBrainz" link opens a three-step flow (`apps/musicbrainz/views.py`): search → preview the
exact before/after diff → apply. Nothing is ever written without that explicit preview step, and
the chosen MusicBrainz recording is cached (`MusicBrainzRecording`) so re-applying it later
doesn't refetch. Always available, regardless of the "MusicBrainz enabled" setting below.

**Automatic enrichment.** Right after a track finishes downloading (and isn't a duplicate skip —
those point at an already-known file), the app automatically looks it up on MusicBrainz. Only a
high-confidence match (MusicBrainz's own relevance score ≥ 90) is accepted, and it only ever
*fills in* the track's MusicBrainz ID and, if blank, its album — it never overwrites a
title/artist/album that's already populated. That still only happens through the manual review
flow above. See `apps.musicbrainz.services.auto_enrich_track()`.

**Background sweep.** A Celery Beat task (`apps.musicbrainz.tasks.musicbrainz_sweep_task`, every
`MUSICBRAINZ_SWEEP_INTERVAL_SECONDS`, default 5 minutes) looks for tracks that have never been
checked against MusicBrainz at all — e.g. downloaded before this feature existed — and runs the
same automatic enrichment on a small batch of them (`SiteSettings.musicbrainz_sweep_batch_size`,
default 5). It deliberately does nothing while any download batch is still active, so it never
competes with real downloads for a worker slot or for MusicBrainz's own shared ~1 request/second
budget. Both the sweep and automatic enrichment are gated by the "MusicBrainz enabled" site
setting; the sweep's own batch size is separately adjustable there too.

## Shared import links

A shared link is an invite: it gives someone without an account the right to download music
through this app for a limited time and number of submissions. The guest gets their own simple
page — a URL submission form (same idea as the authenticated one, just capped by the link's own
limits) plus a status view of what they've submitted and a list of the music downloaded through
*their* link, which they can also play back right there. They never see anyone else's downloads,
the full library, or any admin/staff page.

Create one from the "Create shared link" button on `/admin/sharing/sharedimportlink/` (not the
stock "Add" button — the add form is disabled because creating a link needs to generate and show
the raw token, which a plain form field can't do). The raw token is generated server-side and is
**shown to you exactly once**, on the confirmation page right after creation — it isn't stored in
recoverable form (only its SHA-256 hash is kept), so if you lose it, disable that link and create
a new one. The full shareable URL is `https://<your-host>/share/<token>/`.

- `enabled` — flip off (from the regular admin change form) to revoke immediately, without
  waiting for expiry.
- `expires_at` — enforced automatically; expired links return a generic "not available" page,
  never revealing *why* (expired vs. disabled vs. exhausted) to the visitor.
- `maximum_uses` / `uses` — one "use" is one submission (one or more URLs at once counts as one
  use). Once a link is exhausted it stops accepting new submissions but keeps showing what was
  already downloaded through it, right up until it actually expires or is disabled.
- `max_items_per_submission` — how many URLs the guest can submit at once, independent of the
  site-wide per-authenticated-user cap.

Long-expired links are deleted automatically after `SHARED_LINK_CLEANUP_RETENTION_DAYS` by
scheduled cleanup; this never affects `DownloadBatch` rows a link produced (the FK is
`SET_NULL`, so past imports remain visible in admin even after their link is gone).

## Health and diagnostics

- `/health/` — liveness only (process is up).
- `/ready/` — readiness (database and cache reachable). Used by `docker-compose.yml`
  healthchecks.
- `docker compose logs -f worker` — download activity, including the exact spotDL command
  invoked for each track (`apps.downloader.services.spotdl`).
- `/admin/core/auditlog/` — security/administrative event log: logins, failed logins, logouts,
  batch creation/cancellation, item retries, shared-link creation/use/disablement, read-only in
  admin by design (never edited or deleted through the UI).

## Cleanup

Runs automatically via Celery Beat every `CLEANUP_INTERVAL_SECONDS` (default: every 6 hours);
run it manually with `docker compose exec web python manage.py cleanup`. See
`apps/core/cleanup.py` for exactly what it touches — summarized in `docs/CONFIGURATION.md`.
