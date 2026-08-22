# Troubleshooting guide

## Login/submission returns 403 "CSRF verification failed" or "Origin checking failed"

You're accessing the site through a hostname/IP that isn't in `DJANGO_CSRF_TRUSTED_ORIGINS`.
Django's CSRF protection checks the request's `Origin` header against this list (full origins,
including scheme — `https://music.example.com`, not just the hostname). Add every hostname/IP
you actually use — including a LAN IP if you browse the site from another device on your
network — to `DJANGO_CSRF_TRUSTED_ORIGINS` in `.env`, then:

```bash
docker compose up -d web
```

(Recreating just `web` is enough — `nginx`, `db`, `redis`, `worker`, `beat` don't need to
restart for a Django-only setting.)

## `docker compose ps` shows nginx as "unhealthy" even though the site works

Check `docker inspect <container> --format '{{json .State.Health}}'` for the actual healthcheck
output. If you see `wget: can't connect to remote host: Connection refused` against
`http://localhost/...`, this is a known musl/Alpine resolver quirk — `localhost` can resolve to
`::1` (IPv6) first even though nginx only binds IPv4 in this project's config, so the
healthcheck itself fails even though real traffic (which arrives already addressed to the
container's actual IP) works fine. The healthchecks in `docker-compose.yml` already use
`127.0.0.1` explicitly to avoid this — if you've customized them and reintroduced `localhost`,
switch back.

## A batch stays stuck in "resolving" or an item stuck in "downloading" forever

Check `docker compose logs worker` for the actual spotDL invocation and its output. Common
causes:

- **No outbound network access** to Spotify's API or the configured audio provider (YouTube by
  default) from the `worker` container — spotDL will hang until `SPOTDL_DOWNLOAD_TIMEOUT`
  (default 600s), then the item is marked `failed` and retried up to `SPOTDL_MAX_RETRIES`
  times before giving up.
- **Worker crashed or was replaced mid-task** (e.g. `docker compose up -d --build` landing
  exactly while a task was running, not just idle, or a plain `docker restart`/`docker compose
  restart worker`). State lives in PostgreSQL, not worker memory, so this is recoverable — but
  reproduced for real during development: a batch stuck at 0 items forever, because
  `resolve_batch_task` never got the chance to create any and, by Celery's own default, was
  never redelivered to try again either. `CELERY_TASK_ACKS_LATE` (`config/settings/base.py`) is
  the actual fix — it tells Celery to only remove a task from the Redis queue once it
  *finishes*, so a task genuinely in flight when a worker dies gets redelivered instead of
  silently lost (both `resolve_batch_task` and `download_item_task` are already written to
  safely re-run from scratch — see their docstrings).

  **How long redelivery actually takes** is a second, separate setting, easy to miss:
  `CELERY_VISIBILITY_TIMEOUT_SECONDS` (default 1800s). With Redis as the broker, `acks_late`
  only removes a message once a task finishes — a message that's been delivered but not yet
  acked just sits marked "in flight" until this many seconds pass, at which point Celery assumes
  the worker that had it is gone and redelivers it to whichever worker is currently polling.
  Left at Celery's own built-in default (3600s, a full hour!) rather than this project's own
  setting above, an item stuck "downloading" after a worker restart would eventually resume on
  its own, but reproduced for real: for up to an hour it just sits there doing nothing, which
  reads as "never resumes at all" rather than "resumes, slowly." Verified for real: killed a
  worker mid-download (`docker kill -s SIGKILL`) with a shortened visibility timeout, restarted
  it, and watched the exact same task get redelivered and complete successfully. If downloads
  still seem to hang for a long time after a restart specifically, lower
  `CELERY_VISIBILITY_TIMEOUT_SECONDS` further — just keep it comfortably above
  `SPOTDL_DOWNLOAD_TIMEOUT` plus finalize overhead, or a still-legitimately-running download
  risks being redelivered to a second worker while the first is still working on it.

  Scheduled cleanup is the safety net underneath that, for anything still stuck despite it (an
  older deploy without the setting above, or any other way a task could still go missing):
  `apps.core.cleanup.cleanup_abandoned_items` fails any *item* stuck in an in-progress status
  after 2× `SPOTDL_DOWNLOAD_TIMEOUT`, and `apps.core.cleanup.cleanup_abandoned_batches` fails any
  *batch* that never got past `pending`/`resolving` with zero items after 2× `SPOTDL_RESOLVE_TIMEOUT`.
  After either, the batch/item can be retried (or a fresh batch submitted) from the UI. Run it
  immediately rather than waiting for the schedule with `docker compose exec web python manage.py cleanup`.

  **A batch's items genuinely stuck `pending` with no error and no progress at all** —
  reproduced for real, on a batch whose remaining items had *no* trace anywhere: not in the
  Redis queue (`redis-cli llen celery`), not "active"/"reserved" on any worker (`celery -A
  config inspect active`/`reserved`), and no `unacked` key in Redis either (ruling out them just
  waiting out `CELERY_VISIBILITY_TIMEOUT_SECONDS` above) — some had simply gone missing, most
  likely during a worker restart. The exact mechanism wasn't pinned down with certainty (Redis
  itself never restarted, ruling out a lost/unpersisted queue), but the practical fix is the
  same regardless of cause: `cleanup_abandoned_items` (above) already detects exactly this state
  — an in-progress item older than 2× its own timeout — it just hadn't run yet, because
  `CLEANUP_INTERVAL_SECONDS` defaulted to 6 hours. Lowered to 15 minutes by default; this doesn't
  make cleanup more aggressive (an item is only ever "stuck" once it's older than its own 2×
  timeout, unchanged), only shortens how long a genuinely stuck one can sit unnoticed. Until that
  runs, re-queue by hand: `docker compose exec web python manage.py shell -c "from
  apps.downloader.models import DownloadItem, ItemStatus; from apps.downloader.tasks import
  download_item_task; [download_item_task.delay(i.id) for i in
  DownloadItem.objects.filter(batch_id=<id>, status=ItemStatus.PENDING)]"`.

- **A genuinely large playlist takes longer to resolve than downloading ever would** —
  reproduced for real: a 150-track Spotify playlist took ~624s just to resolve (look up every
  track's metadata before any download starts), which used to raise a spurious
  `SpotDLTimeoutError` because resolving shared the same, much shorter `SPOTDL_DOWNLOAD_TIMEOUT`
  (600s default) meant for a single track. Resolving now has its own, separate
  `SPOTDL_RESOLVE_TIMEOUT` (default 2400s) that scales with playlist size instead — see
  `apps/downloader/services/spotdl.py`'s `resolve_source()`. If you regularly submit playlists
  large enough to still exceed that (especially after raising `MAX_TRACKS_PER_SOURCE` well above
  its own default of 500), raise `SPOTDL_RESOLVE_TIMEOUT` further.

  **Watching a slow resolve live:** before this, a resolve in progress produced *no* log output
  at all until it finished or timed out, minutes later — indistinguishable from a genuine hang.
  `docker compose logs -f worker` now shows, as it happens: every raw line spotDL itself prints
  while resolving (`spotDL resolve output (<url>): ...`), a "still running" heartbeat every 30s
  with the elapsed time and bytes read so far (so a silent gap is visible as a gap, not just
  inferred after the fact), and a final `Resolved <url> in <N>s: <M> track(s) found` line. Also
  logged per source within a multi-URL batch: `resolve_batch_task: batch <id> resolving source
  <i>/<n>: <url>`. If a resolve seems stuck, check these logs first — steady output (or a
  reasonably paced heartbeat) means spotDL is genuinely still working, not stuck.

## Items reach "downloading" and fail every time with `HTTP Error 403: Forbidden`

The batch resolves fine (spotDL/Spotify's own API works — you get real titles/artists/albums),
but every item fails with something like:

```
AudioProviderError: YT-DLP download error - https://www.youtube.com/watch?v=...
AudioProviderError: ERROR: unable to download video data: HTTP Error 403: Forbidden
```

This is the audio provider (YouTube by default) rejecting the actual media fetch, not a bug in
this application — resolving a track's metadata and downloading its audio are different network
calls to different services, and only the second one is failing here. Two distinct causes
produce this same symptom, and it's worth telling them apart:

**1. Missing Deno.** Recent yt-dlp versions need a JS runtime (Deno) installed to solve
YouTube's player-challenge and obtain a valid PO token; without it you'll often see spotDL warn
`Some YouTube downloads require Deno. Run spotdl --download-deno or install Deno system-wide`
right before the 403. This project's `Dockerfile` installs Deno system-wide for exactly this
reason — if you're hitting this on an image built before that was added, `docker compose up -d
--build` picks it up. Confirm it's present with `docker compose exec worker deno --version`.

**2. The outbound IP itself is blocked/deprioritized.** YouTube (and SoundCloud, and some
lyrics providers — Genius/MusixMatch) increasingly rate-limit or outright reject requests from
IP ranges associated with cloud/datacenter hosting, independent of whether Deno solved the
challenge correctly. If you see the *same* 403 (or a 429) across **multiple different
providers** — check whether Genius/MusixMatch lyric lookups are also failing in the debug log —
that's the signature of this, not a fixable code path in this app. It's a property of *where*
the `worker` container's outbound traffic originates. In practice: a residential IP (a home
server, a VPS from a provider with a clean IP reputation) is much less likely to hit this than a
shared/cloud/CI IP range. Changing the network the `worker` container's traffic actually
egresses from is the real fix; if you have a SOCKS5/HTTP proxy with a non-blocked exit IP, set
`SPOTDL_PROXY` (e.g. `socks5://host:port`) to route spotDL's traffic through it.

**3. A yt-dlp stable release is itself temporarily broken.** yt-dlp ships very frequently to
keep up with YouTube's changes, and occasionally a stable release regresses (breaks downloads
outright) before the next one fixes it — this happened for real during this project's own
development: every download failed with the errors above until yt-dlp's own next release landed,
completely unrelated to Deno or IP blocking. The `Dockerfile` always installs the latest
**stable** yt-dlp (`pip install -U "yt-dlp[default]"`, on top of whatever older version spotDL's
own dependency pin would bring in) specifically so this project doesn't get stuck on an old,
possibly-broken pin — `docker compose up -d --build` picks up whatever is current at build time.
If you hit this exact symptom right after a yt-dlp release and a rebuild doesn't help because the
*current* stable is the broken one, yt-dlp's own guidance is to install its latest pre-release
until the next stable ships: temporarily change the `Dockerfile`'s yt-dlp line to
`pip install -U --pre "yt-dlp[default]"`, rebuild, and revert once a working stable release is out
— don't leave `--pre` in permanently, it trades "possibly broken" for "possibly unstable in a
different way."

## Switching audio provider after a few failed attempts

By default this app tries `SPOTDL_PRIMARY_AUDIO_PROVIDER` (YouTube Music) for the first
`SPOTDL_PRIMARY_PROVIDER_ATTEMPTS` attempts on a track, then switches to
`SPOTDL_FALLBACK_AUDIO_PROVIDERS` (e.g. `soundcloud`) for any attempts after that — see
`docs/CONFIGURATION.md`. Each item's current provider/attempt is visible in its "current stage" text (`downloading via
youtube-music` vs. `downloading via soundcloud`), and the exact command/output for a given
attempt is on its `DownloadItem` row in the Django admin (`last_command`/`last_output` — there is
no user-facing panel for this, only the admin).

Note that a fallback provider isn't a guaranteed fix, just a different set of tradeoffs:
SoundCloud in particular marks many official/major-label uploads `DRM protected` and will refuse
to serve them to any downloader, spotDL included — that's SoundCloud's own content protection,
not related to the network-blocking issue above. Less mainstream/indie tracks are more likely to
succeed there.

To tell these two apart yourself: `docker compose exec worker sh` and run `spotdl download
<a-real-track-url> --output /tmp/test/{title}.{output-ext} --headless --log-level DEBUG`
directly — the full traceback and any "requires Deno" hint will be visible instead of the
2000-character-truncated `error_message` stored on the `DownloadItem` row.

## "Too many login attempts" / "Too many requests" (429/403) while testing

Rate limiting (CLAUDE.md #20) is IP-based and shared across all users behind the same IP/proxy.
If you're testing from a script or repeatedly resetting a password, you can hit it. Limits
reset after their window (5 minutes for login/shared-link submission) or clear the Redis cache:

```bash
docker compose exec redis redis-cli FLUSHDB
```

(This also clears the MusicBrainz rate-limit throttle timestamp and any cached `SiteSettings` —
harmless, both are cheap to reconstruct.)

## Emails aren't arriving

1. Confirm `DJANGO_EMAIL_BACKEND` is actually set to
   `django.core.mail.backends.smtp.EmailBackend` — the default (`console.EmailBackend`) prints
   emails to `docker compose logs web` instead of sending them, which is easy to mistake for a
   delivery failure.
2. Use the admin "Send test email" button (`/admin/core/sitesettings/`) or
   `docker compose exec web python manage.py send_test_email you@example.com` — the error
   message it returns on failure is usually the actual SMTP rejection reason.
3. Check `EMAIL_USE_TLS`/`EMAIL_USE_SSL` — exactly one should be `true` for most providers, not
   both.

## MusicBrainz search returns nothing / is slow

MusicBrainz enforces roughly one request per second per client; this app throttles itself to
match (`apps/musicbrainz/services.py`), tracked via a Redis-cached timestamp shared across
workers — so a burst of searches queues up rather than getting rate-limited by MusicBrainz
itself, but the second and later requests in a burst will visibly wait. This is expected
behavior, not a bug.

## A track shows as a duplicate but I don't think it should be

- **"Duplicate"** (exact) means byte-identical file content (SHA-256 match) — this is never a
  false positive short of an actual hash collision.
- **"Possible duplicate"** (probable) means the artist and title normalize to the same text
  (case/punctuation/accents ignored) as something already in the library, but the file content
  differs — a genuinely different recording (live version, remaster, different source) will
  trigger this. It's informational; nothing is skipped or blocked because of it.

## `manage.py makemigrations` wants to create a migration I didn't expect

Run `manage.py makemigrations --check --dry-run` in CI/before committing — a clean result means
models and migrations agree. If it's unexpected, check whether you changed a field's default,
`help_text`, or similar metadata-only attribute (these do generate migrations in Django, even
though they don't change the database schema) before assuming something is actually wrong.
