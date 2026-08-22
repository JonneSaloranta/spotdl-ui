# spotDL Web UI

A self-hosted web application that provides a simple UI for downloading music through
[spotDL](https://github.com/spotDL/spotify-downloader), with a persistent download history,
SHA-256-based duplicate detection, automatic + on-demand MusicBrainz metadata enrichment, and
admin-managed temporary shared import links.

Built with Django, PostgreSQL, Redis, Celery, and Bootstrap 5. See `CLAUDE.md` for the full
implementation specification this project follows, and `docs/` for supporting design notes.

## Status

**1.0.0 — stable.** See `CHANGELOG.md` for what shipped in this release and `docs/ROADMAP.md`
for what's deliberately left for later. Working end-to-end, verified against real Spotify/
YouTube URLs (`docs/SPOTDL_VERIFICATION.md`):

- User authentication (login/logout/password reset), per-user theme preference, light/dark/
  automatic theming, English + Finnish UI.
- URL submission (single and batch), SSRF-hardened URL validation, async resolution and
  download via Celery, live HTMX-polled progress, retry/cancel.
- SHA-256 hashing, exact + probable-metadata + source-identifier duplicate detection,
  atomic finalization into the music library.
- Admin-managed temporary shared import links (token-hashed, rate-limited, auto-expiring).
- MusicBrainz integration: rate-limited/retrying/caching client, recording search, a
  three-step staff-only review → preview-diff → apply flow that never overwrites metadata
  without an explicit before/after confirmation, automatic high-confidence enrichment right
  after each new download, and a slow background sweep (only while no batch is active) for
  tracks that were never checked. (Navidrome integration was removed by request — see the
  amendment note at the top of `CLAUDE.md`.)
- PWA: web app manifest (branded from `SiteSettings`), a service worker that precaches
  the static app shell and one offline fallback page (never caches authenticated/dynamic
  pages), and generated icons.
- A searchable, paginated `/library/` page for any logged-in user, with in-browser playback
  and an exact-duplicate badge.
- SMTP batch-completed/failed email notifications (one per batch, never per track, respecting
  per-user preferences), an admin "Send test email" action, and a `send_test_email` management
  command.
- Security/administrative event logging wired up end-to-end (login, failed login, logout,
  batch/shared-link/item actions — `AuditLog`, `/admin/core/auditlog/`), and IP-based rate
  limiting on the login view.
- Scheduled cleanup (`apps.core.cleanup`, every 15 minutes via Celery Beat or
  `manage.py cleanup`): abandoned in-progress items/batches, orphaned temp download
  directories, long-expired shared links — never touches library files or `TrackFile` rows.
- `docker compose up -d` brings up web/worker/beat/db/redis/nginx with healthchecks,
  automatic migrations, and static file collection.

Known gap: a dedicated simplified admin settings UI beyond Django admin + `/settings/` is not
built (see `docs/ADMIN_GUIDE.md` for what's already covered).

## Quick start (Docker Compose)

```bash
cp .env.example .env
# edit .env: set DJANGO_SECRET_KEY and POSTGRES_PASSWORD at minimum

docker compose up -d --build

# first run only
docker compose exec web python manage.py createsuperuser
```

Visit `http://localhost:${HTTP_PORT:-8000}/`. Health checks are at `/health/` (liveness) and
`/ready/` (readiness — checks the database and cache).

## Configuration

All configuration is via environment variables — see `docs/ENVIRONMENT.md` for the full list
and `.env.example` for a filled-in template. Nothing secret (API keys, SMTP passwords,
database credentials) is ever stored in application models or returned by the UI/API.

Key variables to set for a real deployment:

- `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`
- `POSTGRES_PASSWORD`
- `SECURE_SSL_REDIRECT=true`, `SESSION_COOKIE_SECURE=true`, `CSRF_COOKIE_SECURE=true`,
  `SECURE_HSTS_SECONDS=31536000` once served over HTTPS
- `SPOTDL_*` for output format/bitrate/concurrency
- `MUSICBRAINZ_*` for the metadata-enrichment integration
- `EMAIL_*` for SMTP (password reset, batch notifications)

## Architecture

```
nginx (reverse proxy, static/media)
  -> web (Django + Gunicorn)
worker (Celery)  -\
beat (Celery Beat)-+-> redis (broker/cache) + db (PostgreSQL)
```

`web`, `worker`, and `beat` share one Docker image (`Dockerfile`) and differ only in the
command `docker-compose.yml` gives them. Only `web` runs database migrations
(`RUN_MIGRATIONS=true`), so multiple containers never race to apply them concurrently.

Download data flow (see `CLAUDE.md` §5 for the full spec):

1. A user (or a shared-link visitor) submits one or more URLs — validated against an
   allow-list of hosts and checked for SSRF (private/loopback/link-local targets are
   rejected) in `apps/downloader/validators.py`.
2. A `DownloadBatch` is created and `resolve_batch_task` is queued.
3. The worker calls `spotdl save` (via `apps/downloader/services/spotdl.py`) to expand the
   URL into individual tracks without downloading audio, creating one `DownloadItem` per
   track.
4. Each item is downloaded independently (`download_item_task`), so one failing track never
   blocks the rest of a playlist.
5. On success, `apps/library/services.py` hashes the file (SHA-256), checks for exact and
   probable-metadata duplicates, and atomically moves it into `MUSIC_ROOT` — never
   overwriting an existing file.
6. The UI polls batch/item status via HTMX every few seconds until the batch finishes.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -r requirements/dev.txt
cp .env.example .env   # point POSTGRES_HOST/REDIS_URL at local or Dockerized services

.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Run the test suite (uses a real PostgreSQL database — external services are always mocked,
per `CLAUDE.md` §25):

```bash
.venv/bin/pytest
```

## spotDL version pinning

The exact spotDL command syntax used by `apps/downloader/services/spotdl.py` was verified
against the installed version rather than guessed — see `docs/SPOTDL_VERIFICATION.md` for
what was checked and how to re-verify after a version bump. The version is pinned exactly in
`requirements/base.txt`.

## Documentation

- `docs/INSTALLATION.md` — install and upgrade steps.
- `docs/CONFIGURATION.md` — every environment variable that's worth understanding before you deploy.
- `docs/ADMIN_GUIDE.md` — site settings, users, shared links, MusicBrainz, cleanup.
- `docs/USER_GUIDE.md` — using the app as an end user.
- `docs/BACKUP_RESTORE.md` — backing up the database and the music library (two separate things).
- `docs/DEVELOPMENT.md` — local dev setup, running tests, project layout.
- `docs/TROUBLESHOOTING.md` — the issues you're most likely to actually hit.
- `docs/SPOTDL_VERIFICATION.md` — what's verified vs. assumed about spotDL's CLI/output.
- `docs/FEATURES.md` — the full feature list, current as of this release.
- `docs/DATA_MODEL.md`, `docs/SECURITY.md`, `docs/TEST_PLAN.md` — data model, security posture,
  and test coverage as actually built.
- `CHANGELOG.md` — what changed in each release.

## Known limitations

- The PWA offline shell caches static assets and one offline fallback page, but the app is
  not meant to be usable offline beyond that — downloads always need a live server.
- The admin settings UI is Django admin only; a dedicated simplified settings page (CLAUDE.md
  §11) is not built yet.

## License

[GPL-2.0-or-later](LICENSE). Chosen specifically because `mutagen` (used directly in
`apps/library/services.py` for audio tag reading, not invoked as a subprocess like spotDL) is
itself licensed GPL-2.0-or-later — a combined work distributing it must be compatible with that.
spotDL, Django, Celery, and the other dependencies are all permissively licensed (MIT/BSD/
Apache-2.0/LGPL) and impose no such requirement on their own.
