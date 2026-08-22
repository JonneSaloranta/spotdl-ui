# Development guide

## Local setup (without Docker)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements/dev.txt
```

You still need PostgreSQL and Redis reachable somewhere — the simplest way is two throwaway
containers:

```bash
docker run -d --name spotdlui-dev-pg -e POSTGRES_DB=spotdl_ui -e POSTGRES_USER=spotdl_ui \
  -e POSTGRES_PASSWORD=devpass -p 55432:5432 postgres:16-alpine
docker run -d --name spotdlui-dev-redis -p 56379:6379 redis:7-alpine
```

Create a `.env` (this can differ from the one you use for `docker compose` — keep them as
separate files, e.g. `.env` for Compose and `.env.dev` for this) pointing at `localhost:55432`
/ `localhost:56379` rather than the Docker service names `db`/`redis`, with `DJANGO_DEBUG=true`.
Then:

```bash
set -a && source .env.dev && set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

`STORAGES["staticfiles"]` automatically uses the simpler, non-manifest `StaticFilesStorage`
whenever `DEBUG=true` (see `config/settings/base.py`), so you don't need to run
`collectstatic` after editing a static file in dev — only in a `DEBUG=false` build.

## Running tests

```bash
set -a && source .env.dev && set +a
.venv/bin/pytest
```

Uses the real PostgreSQL you pointed `.env.dev` at (pytest-django creates/reuses a
`test_<db>` database there); `--reuse-db` is set in `pytest.ini`, so pass `--create-db` once
after a schema change that isn't a migration, or if the test DB gets into a stale state — e.g.
if you've been running ad-hoc scripts against it directly outside of pytest's transaction
wrapping, which don't roll back.

A coverage report (via `pytest-cov`, configured in `.coveragerc`) prints automatically after
every run — source is restricted to `apps/`, with migrations/tests/management-command
boilerplate excluded. For an HTML report you can click through:

```bash
.venv/bin/pytest --cov-report=html
open htmlcov/index.html  # or just open the file in a browser
```

Every external service (spotDL subprocess, MusicBrainz, SMTP) is mocked in tests —
`unittest.mock` for subprocess/HTTP calls, the `responses` library for MusicBrainz's HTTP API
specifically, Django's `locmem` email backend for mail. No test should ever require network
access or a live MusicBrainz server to pass (CLAUDE.md #25).

## Project layout

```
config/            Django project: settings, root urls, celery.py
apps/
  core/             Health checks, site settings, audit log, PWA, cleanup, notifications
  accounts/         UserProfile, auth views/forms, theme persistence
  downloader/       DownloadBatch/DownloadItem, spotDL service layer, Celery tasks, validators
  library/          TrackFile, hashing, filename sanitization, duplicate detection, finalize
  musicbrainz/      MusicBrainz client, review/preview/apply flow, auto-enrich, sweep task, models
  sharing/          SharedImportLink, public submission view
templates/          One directory per app, plus templates/base.html and templates/admin/...
static/             Vendored Bootstrap/HTMX (no CDN dependency), theme.css, icons
locale/fi/          Finnish translation catalog
docs/               This directory
docker/             entrypoint.sh, nginx.conf
```

Each app's spotDL/HTTP-integration logic lives in a `services.py` (or `services/` package for
`downloader`) — never scattered into views or tasks directly (CLAUDE.md #4). Celery tasks live
in `tasks.py` per app and stay thin: they read/write model state and delegate real work to the
service layer, so the service layer stays independently testable without Celery in the loop.

## spotDL command syntax

Don't guess spotDL's CLI flags or `.spotdl` save-file JSON schema — verify them against the
actually-installed version (`requirements/base.txt` pins it exactly) and record what you
checked in `docs/SPOTDL_VERIFICATION.md`. See that file for the checklist to run through on a
version bump.

## Adding a new environment variable

1. Read it in `config/settings/base.py` via `decouple.config(...)` with a sensible default.
2. Add it to `.env.example` with a comment.
3. Document it in `docs/CONFIGURATION.md` if it's something an operator would plausibly need
   to change (skip purely internal/derived values).

## Commit message format

Commits on `master` drive the automated release below, so they must follow
[Conventional Commits](https://www.conventionalcommits.org/): `<type>: <description>`, e.g.
`fix: resume interrupted downloads faster after a worker restart` or
`feat: add MusicBrainz background sweep`. The `type` decides the version bump:

| Type | Effect |
|---|---|
| `fix:` | patch release (1.0.0 → 1.0.1) |
| `feat:` | minor release (1.0.0 → 1.1.0) |
| `feat!:` / `fix!:` / a `BREAKING CHANGE:` footer | major release (1.0.0 → 2.0.0) |
| `docs:`, `chore:`, `ci:`, `refactor:`, `test:`, `style:`, `perf:` | recorded in the changelog, no version bump on its own |

## Releasing a new version

Fully automated by `.github/workflows/release.yml` ([release-please](https://github.com/googleapis/release-please)) — never bump `VERSION` or edit
`CHANGELOG.md` by hand. Every push to `master` is scanned for Conventional Commits since the
last release; release-please opens (or updates) a PR that bumps `VERSION` and writes the
changelog entry. Merging that PR is what actually cuts the release — the next push to `master`
(the merge itself) creates the git tag and GitHub Release. Configuration:
`release-please-config.json` / `.release-please-manifest.json`.

## Code style

`ruff check .` runs in CI (`.github/workflows/ci.yml`) on every PR/push to `dev`/`master`; run
it locally before pushing (`ruff` is in `requirements/dev.txt`). Match the existing code:
docstrings that explain *why* a piece of
code exists (usually citing the relevant `CLAUDE.md` section) rather than restating what the
code obviously does, small service-layer functions over large view methods, and dataclasses for
structured return values instead of raw dicts/tuples where a function returns more than one
piece of information.
