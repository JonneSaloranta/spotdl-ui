# Backup and restore guide

There are two independent things to back up, and a Docker volume is **not** a backup of either
by itself (CLAUDE.md #28) — it's still a single copy on the same host as everything else.

1. **The PostgreSQL database** — users, batches/items, library metadata (`TrackFile`,
   `DuplicateMatch`), the MusicBrainz cache, shared links, audit log, site settings.
2. **The music library** (`MUSIC_ROOT`, the `music` subdirectory of the `library_data` volume) —
   the actual audio files.

Losing #1 without #2 means you keep your files but lose all history/metadata/dedup state.
Losing #2 without #1 means the database references files that no longer exist. Back up both,
ideally on the same schedule, to somewhere off the host.

## Database: backup

```bash
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > backup-$(date +%Y%m%d).dump
```

`-Fc` (custom format) is compressed and restorable selectively; keep it over a plain SQL dump
unless you specifically need a human-readable file.

## Database: restore

Restoring drops and recreates the schema, so do this only against a database you mean to
overwrite (a fresh install, or one you're deliberately rolling back):

```bash
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < backup-20260101.dump
```

Then restart `web`/`worker`/`beat` so they pick up a clean state:

```bash
docker compose restart web worker beat
```

## Database: integrity check

```bash
docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
  SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;
"
```

A quick sanity check after a restore — confirms tables exist and have roughly the row counts
you expect. For a real integrity check, `pg_dump` itself failing is usually the first sign of
corruption; `pg_amcheck` (bundled with PostgreSQL 14+) can check indexes/heaps more thoroughly
if you suspect disk-level corruption.

## Music library: backup

The library lives under `MUSIC_ROOT` (`/data/music` inside the containers — the `music`
subdirectory of the `./library_data` host bind mount by default; it shares that mount with
`DOWNLOAD_TEMP_ROOT` deliberately, see the comment in `docker-compose.yml`). Because it's a host
bind mount, not an opaque Docker-managed volume, it's just an ordinary directory on the host —
back it up directly with whatever file-level tool you already use (`rsync`, `restic`, `borg`, a
cloud sync agent, etc.), no `docker run`/container detour needed. A minimal example that backs
up only the `music` subdirectory, not the scratch `downloads` one next to it:

```bash
tar czf backups/music-$(date +%Y%m%d).tar.gz -C ./library_data/music .
```

Do this while downloads are idle if possible — an in-progress download writes to
`DOWNLOAD_TEMP_ROOT`, not `MUSIC_ROOT`, and files are only moved into `MUSIC_ROOT` after they're
fully written and hashed (CLAUDE.md #21's atomic finalization), so a mid-flight backup won't
catch a half-written file either way — but a consistent snapshot is still easier to reason
about.

## Music library: restore

Extract the archive back into the `./library_data` bind mount's `music` subdirectory (or wherever
`MUSIC_ROOT` points). The `TrackFile` database rows are the source of truth for what the app
*thinks* is in the library; if you restore files without restoring a matching database, or vice
versa, they'll be out of sync until reconciled — either restore both together from the same
point in time, or run a library scan afterwards (`manage.py scan_library --full`, or the
"Full sync" button in the navbar for staff — see `docs/ADMIN_GUIDE.md`'s "Library scan") to
rebuild `TrackFile` rows from whatever is actually on disk.

## What's *not* backed up by the above

- `.env` — not app data, but losing it means losing `DJANGO_SECRET_KEY` (invalidates all
  sessions) and every credential. Back it up separately, encrypted, outside the repo.
- Redis — purely ephemeral (Celery broker/result backend + rate-limit counters + the
  MusicBrainz throttle timestamp). Losing it loses in-flight task state, not data; downloads
  already running will need to be resubmitted, but nothing in the permanent library is at risk.
