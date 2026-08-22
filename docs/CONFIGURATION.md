# Configuration guide

All configuration is environment variables, read once at process start (see
`config/settings/base.py`). `docs/ENVIRONMENT.md` lists every variable by category as the
original specification; this document explains the ones most worth understanding before you
deploy. `.env.example` has a working default for every one of them.

## Required, no safe default

| Variable | Notes |
|---|---|
| `DJANGO_SECRET_KEY` | Long, random, unique per installation. Rotating it invalidates all sessions and signed tokens. |
| `POSTGRES_PASSWORD` | Must match between the `db` service and every service that connects to it. |

## Security-relevant

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_DEBUG` | `false` | Never `true` in production — it leaks stack traces (source, settings values, request data) to any visitor who triggers an error. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated. Django rejects requests with a `Host` header not in this list. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated **full origins** (`https://music.example.com`, scheme included) that are allowed to submit forms. Required for every hostname/IP you actually access the site through, or POST requests (login, submissions) return 403 — see `docs/TROUBLESHOOTING.md`. |
| `SECURE_SSL_REDIRECT` / `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | `false` | Enable once served over HTTPS. |
| `SECURE_HSTS_SECONDS` | `0` | Set to a real value (e.g. `31536000`) once HTTPS is confirmed working — HSTS is hard to safely undo, so verify HTTPS first. |

## spotDL

| Variable | Default | Notes |
|---|---|---|
| `SPOTDL_AUDIO_FORMAT` | `mp3` | One of spotDL's supported output formats. |
| `SPOTDL_BITRATE` | `320k` | One of spotDL's `--bitrate` choices (`128k`, `320k`, `auto`, `disable`, ...), or empty to omit the flag entirely and let spotDL use its own default. `disable` is spotDL's own way to remove the constraint — it keeps the source's original bitrate rather than transcoding to a fixed one (and skips conversion altogether for m4a/opus output). |
| `SPOTDL_OUTPUT_TEMPLATE` | `{artist}/{album}/{track-number:02d} - {title}.{output-ext}` | spotDL's own filename template syntax — only controls the filename *within* the server-controlled temp directory; the final library path is built separately and sanitized (`apps/library/filenames.py`), never taken from this template directly. |
| `SPOTDL_MAX_RETRIES` | `3` | Retries per track before a `DownloadItem` is marked `failed`. Total attempts = this + 1. Must exceed `SPOTDL_PRIMARY_PROVIDER_ATTEMPTS` for the fallback provider tier below to ever actually be reached. |
| `SPOTDL_DOWNLOAD_TIMEOUT` | `600` | Seconds before a single-track download is killed as hung. Also drives the abandoned-*item* cleanup threshold (2× this value — see `apps/core/cleanup.py`). |
| `SPOTDL_RESOLVE_TIMEOUT` | `2400` | Seconds before resolving a source (looking up every track in a playlist/album) is killed as hung. Separate from `SPOTDL_DOWNLOAD_TIMEOUT` above because resolving one large playlist legitimately takes much longer than downloading one track — a real 150-track playlist took ~624s. Also drives the abandoned-*batch* cleanup threshold (2× this value). Raise this if `MAX_TRACKS_PER_SOURCE` is raised well above its default. |
| `SPOTDL_PRIMARY_AUDIO_PROVIDER` | `youtube` | **Bootstrap value only** — used the first time `SiteSettings` is created, and as a lower-level fallback for direct use of the spotDL service layer. The real, ongoing control for provider tiering is the admin "Site settings" page (`/settings/`), editable live without a redeploy — see `apps.downloader.tasks.pick_audio_providers()`. Empty means "let spotDL use its own built-in default" for those attempts. |
| `SPOTDL_PRIMARY_PROVIDER_ATTEMPTS` | `3` | Bootstrap value for the same admin-editable setting. How many attempts stay on the primary provider before switching to the fallback tier. Each attempt is a full separate spotDL invocation (driven by the per-item retry loop) — this is why the tiering lives here rather than in spotDL's own `--audio p1 p2` fallback, which only tries each provider once *within* one invocation, never several times before moving on. |
| `SPOTDL_FALLBACK_AUDIO_PROVIDERS` | `` (empty) | Bootstrap value for the same admin-editable setting (see above). Comma-separated, used once the primary tier is exhausted — e.g. `soundcloud`. Empty means no fallback tier: the primary provider just keeps being retried until `SPOTDL_MAX_RETRIES`. |
| `SPOTDL_PROXY` | empty | A SOCKS5/HTTP proxy URL passed to spotDL's `--proxy`. The one lever available when the deployment's outbound IP is itself rate-limited/blocked by the audio provider — see `docs/TROUBLESHOOTING.md`. |
| `DOWNLOAD_CONCURRENCY` | `2` | Passed to the Celery worker's `--concurrency`. |
| `MAX_TRACKS_PER_SOURCE` | `500` | Caps how many tracks a single URL (e.g. one large playlist) can queue — without this, one URL could create unbounded work (CLAUDE.md #20). Extra tracks beyond the cap are simply not queued; the batch's error summary notes it. |

The exact spotDL command-line syntax this app relies on is documented — with what was actually
verified versus assumed — in `docs/SPOTDL_VERIFICATION.md`. Re-read it before bumping
`SPOTDL_VERSION`.

## Storage paths

| Variable | Default | Notes |
|---|---|---|
| `MUSIC_ROOT` | `/data/music` | The permanent library. Back this up — see `docs/BACKUP_RESTORE.md`. |
| `DOWNLOAD_TEMP_ROOT` | `/data/downloads` | Scratch space during download/finalize. Never served over HTTP (see `docker/nginx.conf` — there is deliberately no `location` for it). Safe to lose entirely; scheduled cleanup also prunes orphaned entries here. |

In Docker Compose both are subdirectories (`music`, `downloads`) of a single named volume
(`library_data`) — deliberately one volume, not two, so that finalizing a download can move
the file into the library with a single atomic rename rather than a cross-filesystem copy (see
the comment above the `library_data` mount in `docker-compose.yml`). Mount it from a real disk
in production rather than relying on Docker's default volume location.

| Variable | Default | Notes |
|---|---|---|
| `MUSIC_STREAMING_BACKEND` | `nginx` | How the library page's in-browser playback serves audio bytes. `nginx` hands off to nginx's internal `/protected-music/` location via `X-Accel-Redirect` (efficient, supports seeking via HTTP Range). `direct` streams from Django itself — use this if nginx isn't in front of the app (e.g. `manage.py runserver`, or a different reverse proxy). Either way the request is always authenticated and resolved through the `TrackFile` table first (`apps/library/views.py`) — no raw filesystem path is ever taken from the browser. |

## MusicBrainz

Has sane defaults and needs no account — but set `MUSICBRAINZ_CONTACT` to a real email address.
MusicBrainz's usage policy requires a way to reach the operator of automated clients; the app
appends it to the User-Agent it sends (`apps/musicbrainz/services.py`).

Automatic enrichment and the background sweep for never-checked tracks are both controlled by
the admin-editable `SiteSettings.musicbrainz_enabled`/`musicbrainz_sweep_batch_size` (not
environment variables — see `docs/ADMIN_GUIDE.md`'s "MusicBrainz" section). The one env var here
is `MUSICBRAINZ_SWEEP_INTERVAL_SECONDS` (default `300`) — how often Celery Beat *checks* whether
it's a good time to run the sweep; each actual run still only processes a small batch and only
while no download batch is active.

## SMTP

Defaults to the console backend (emails are printed to the `web` container's logs, nothing is
actually sent) — fine for evaluation, not for production. Set `DJANGO_EMAIL_BACKEND` to
`django.core.mail.backends.smtp.EmailBackend` and fill in `EMAIL_HOST`/`EMAIL_PORT`/
`EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD` to send real mail (password resets, batch-finished
notifications). Verify with the admin "Send test email" button or
`manage.py send_test_email you@example.com`.

## Shared links

| Variable | Default |
|---|---|
| `SHARED_LINK_DEFAULT_TTL_HOURS` | `24` |
| `SHARED_LINK_MAX_TTL_HOURS` | `336` (14 days) |
| `SHARED_LINK_MAX_USES` | `20` |
| `SHARED_LINK_MAX_ITEMS_PER_SUBMISSION` | `25` |
| `SHARED_LINK_CLEANUP_RETENTION_DAYS` | `30` — how long an expired link's row is kept for audit purposes before scheduled cleanup deletes it. |

## Cleanup

| Variable | Default |
|---|---|
| `CLEANUP_INTERVAL_SECONDS` | `900` (15 min) — how often Celery Beat runs `apps.core.tasks.run_cleanup_task`. Kept short deliberately: this is what detects items/batches stuck with no worker actually processing them (see docs/TROUBLESHOOTING.md) — an item still only counts as stuck once it's older than 2x its own timeout regardless, so running the check often doesn't make it more aggressive, only cuts how long a genuinely stuck one can sit unnoticed. |

Cleanup can also be run manually: `docker compose exec web python manage.py cleanup`.
