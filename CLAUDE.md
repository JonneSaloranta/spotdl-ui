# Claude Code Instructions — spotDL Web UI

> **Amendment (2026-08-21): Navidrome integration removed.** The project owner
> explicitly decided against Navidrome integration and asked for it to be removed
> entirely — it had never actually been configured in this deployment (no
> `NAVIDROME_*` environment variables were ever set) and wasn't surfaced anywhere
> in the regular UI. `apps/navidrome/` and every setting, task, cleanup job,
> and admin action tied to it have been deleted. Section 8 below and every
> other Navidrome reference throughout this document describe the *original*
> design and are superseded by this decision — kept only for historical
> context, not as something to re-implement. If Navidrome integration is ever
> wanted again, treat section 8 as a starting design, not as already-decided.

## 1. Goal

Build a production-quality, self-hosted web application that provides a simple UI for downloading music through spotDL.

The application must:
- accept one or multiple URLs;
- accept playlists and other supported spotDL sources;
- queue downloads asynchronously;
- show live download status;
- keep a persistent database of downloads and imported songs;
- detect duplicates using cryptographic hashes and metadata;
- optionally compare the local library against Navidrome;
- enrich/update metadata using MusicBrainz;
- support administrator-created temporary shared import links;
- support PWA installation;
- support light/dark/automatic themes;
- support multiple languages;
- support SMTP;
- run entirely through Docker Compose.

Do not build a frontend SPA unless there is a strong technical reason. Prefer Django templates + Bootstrap 5 + progressive enhancement/HTMX if useful.

All application source code, Python identifiers, model names, comments, documentation and internal configuration names must be in English.

Every user-visible string must be translatable. Use Django gettext/i18n rather than hard-coded UI strings.

## 2. Important design principles

1. Do not trust user input.
2. Never execute shell commands through `shell=True` with untrusted input.
3. Use subprocess argument arrays when invoking spotDL.
4. Validate URLs and reject dangerous protocols.
5. Keep downloads outside the web container's executable/source directories.
6. Make download jobs idempotent.
7. Use database transactions for state changes.
8. Use Redis/Celery for long-running work; never block HTTP requests with downloads.
9. Make all workers restart-safe.
10. Do not expose Navidrome, Redis, PostgreSQL or SMTP credentials to the browser.
11. Do not expose arbitrary filesystem paths through the UI.
12. Use CSRF protection everywhere applicable.
13. Add authentication and authorization to administrative functions.
14. Log security-sensitive events.
15. Do not store external service passwords in plaintext in normal application records.
16. Design for interrupted downloads and worker restarts.
17. Prefer soft deletion/audit history over destructive deletion where useful.

## 3. Architecture

Use Docker Compose with at least:

- web: Django + Gunicorn
- worker: Celery worker
- beat: Celery Beat if scheduled jobs are needed
- db: PostgreSQL
- redis: Redis
- nginx: reverse proxy/static/media serving

Optional:
- dedicated scanner/metadata worker if useful.

The application must work with `docker compose up -d`.

Use environment variables for:
- Django secret key
- debug
- allowed hosts
- database credentials
- Redis URL
- spotDL executable/configuration
- media/download root
- Navidrome URL
- Navidrome API credentials/token
- MusicBrainz settings
- SMTP settings
- shared-link defaults
- language/timezone settings

Provide `.env.example`.

## 4. spotDL integration

Create a dedicated service layer, for example:

`apps/downloader/services/spotdl.py`

Do not scatter spotDL subprocess logic through views/tasks.

The service should:
- validate input;
- build safe subprocess arguments;
- run spotDL;
- capture stdout/stderr;
- parse useful progress information;
- return structured results;
- support cancellation;
- support retries;
- record exit codes;
- record errors.

The exact spotDL command syntax must be verified against the installed spotDL version rather than guessed.

Pin a tested spotDL version in the Docker image.

Do not assume that spotDL output format is stable. Keep parsing isolated and test it.

## 5. Download workflow

Typical workflow:

1. User enters one or more URLs.
2. Server validates and normalizes them.
3. Create an Import/DownloadBatch record.
4. Resolve the supplied sources.
5. Discover individual tracks.
6. Create DownloadItem records.
7. Queue items through Celery.
8. Worker downloads a track.
9. Worker calculates SHA-256 hash.
10. Worker extracts file metadata.
11. Worker optionally queries MusicBrainz.
12. Worker checks duplicate candidates.
13. Worker stores final file.
14. Worker updates database.
15. Worker optionally refreshes Navidrome status.
16. UI receives/polls status and shows progress.

For playlists, preserve:
- source URL;
- playlist/source title;
- source position;
- original external identifier where available.

A batch must remain useful even if some tracks fail.

## 6. Database

Use PostgreSQL.

Suggested core models:

### User
Use Django's authentication system.

### UserProfile
Optional preferences:
- preferred language
- theme preference
- timezone
- notification settings

### DownloadBatch
Fields should include at least:
- id
- created_by
- created_at
- updated_at
- source_urls
- source_type
- title
- status
- total_items
- completed_items
- failed_items
- cancelled_items
- error_summary

### DownloadItem
Fields:
- id
- batch
- source_url
- source_identifier
- title
- artist
- album
- album_artist
- playlist_name
- status
- progress
- current_stage
- error_message
- retry_count
- created_at
- started_at
- completed_at
- cancelled_at

### TrackFile
Fields:
- id
- path
- filename
- size
- sha256
- duration
- bitrate
- format
- mime_type
- title
- artist
- album
- album_artist
- track_number
- disc_number
- year
- created_at
- updated_at
- last_scanned_at

Add indexes for:
- sha256
- normalized artist/title
- source_identifier
- status

SHA-256 should be the canonical file hash. Optionally support additional hashes later.

### MusicBrainzRecording
Cache MusicBrainz results instead of repeatedly requesting the same data.

### NavidromeTrack
Cache relevant Navidrome information:
- external/id
- path
- title
- artist
- album
- duration
- size if available
- hash if available
- last_seen_at

### SharedImportLink
Fields:
- token
- created_by
- created_at
- expires_at
- enabled
- maximum_uses
- uses
- label
- allowed_actions

Tokens must be cryptographically random and stored securely. Prefer storing a hash of the token if practical.

### AuditLog
Record security/administrative events.

## 7. Duplicate detection

This is a core feature.

Every successfully downloaded local file must be hashed with SHA-256.

Do not rely only on filename.

Duplicate checks should happen at several levels:

1. Exact SHA-256 match.
2. Same normalized artist + title + album.
3. Same source identifier where available.
4. Optional duration similarity.
5. Navidrome comparison.

UI should distinguish:
- exact duplicate;
- probable metadata duplicate;
- new track.

Never silently overwrite an existing file.

Make duplicate behavior configurable:
- skip;
- keep both;
- ask user where possible.

## 8. Navidrome integration

Create a separate Navidrome client service.

Support configuration for:
- URL
- username/token
- password/token
- connection timeout
- SSL verification

Use Navidrome/Subsonic-compatible APIs as appropriate.

Capabilities:
- test connection;
- list/search tracks;
- synchronize library information;
- compare available songs;
- identify matching files;
- optionally calculate local hashes and compare them with known local data.

Important:
Navidrome does not necessarily expose a cryptographic file hash for every song. Do not assume it does.

If a remote hash is unavailable:
- compare normalized metadata;
- compare duration;
- compare path where applicable;
- optionally hash accessible local files.

Provide a manual "Sync Navidrome" action and an optional scheduled synchronization task.

Never make Navidrome synchronization block downloads.

## 9. MusicBrainz integration

Create a dedicated MusicBrainz service.

Requirements:
- configurable API base URL;
- respectful User-Agent identifying the application;
- rate limiting;
- caching;
- retry/backoff;
- timeout;
- no excessive requests.

Use MusicBrainz to:
- search recordings;
- search releases;
- identify artists;
- improve title/artist/album metadata;
- retrieve MBIDs;
- optionally retrieve cover-art references where legally/technically appropriate.

Never blindly overwrite user-approved metadata.

Provide a review/preview step for metadata changes where practical.

Store MBIDs.

## 10. Shared import links

Admins can create temporary links that allow another person to submit/import song lists without having a normal account.

Admin settings must define:
- default availability duration;
- maximum duration;
- maximum uses;
- whether anonymous use is allowed;
- maximum URLs/items per submission.

The shared page should:
- clearly show that it is a temporary shared import page;
- accept URLs;
- display validation results;
- allow submission;
- never expose admin pages;
- never expose other users' downloads;
- rate-limit requests.

A shared link must expire automatically.

Do not make a shared link equivalent to an authenticated account.

## 11. Admin panel

Use Django admin for deep administration, but also provide a simple application settings/admin UI where appropriate.

Admins should be able to manage:
- users;
- download settings;
- shared-link defaults;
- SMTP settings;
- Navidrome configuration;
- MusicBrainz settings;
- allowed file formats;
- download concurrency;
- retention;
- cleanup;
- languages;
- site branding;
- PWA settings;
- maintenance mode.

Do not put secrets into ordinary templates.

## 12. Download status UI

The main page should be simple.

Suggested layout:

- URL input
- "Add another URL"
- "Import playlist"
- download options
- submit button

Below it:

- Active downloads
- Queue
- Completed
- Failed
- Duplicate/skipped

Each item should show:
- title
- artist
- album
- status
- progress
- speed if available
- ETA if available
- current stage
- error
- retry button
- cancel button

Use HTMX/AJAX polling or Server-Sent Events for live updates.

Do not require a websocket stack unless it is actually justified.

## 13. UI/UX

Use Bootstrap 5.

Mobile-first.

Keep the main workflow to as few controls as possible.

Responsive design must work on:
- phone;
- tablet;
- desktop.

Use Bootstrap components:
- navbar
- cards
- forms
- alerts
- progress bars
- modals
- dropdowns
- offcanvas menus

Add accessible labels, keyboard navigation, focus states and semantic HTML.

Use icons consistently, preferably Bootstrap Icons.

## 14. Themes

Support:
- Light
- Dark
- Automatic/system

Persist the user's preference.

Do not duplicate the entire CSS framework for themes.

Use Bootstrap-compatible CSS variables where possible.

Provide a visible theme selector.

## 15. Internationalization

All visible strings must use Django translation mechanisms.

Initial languages:
- English
- Finnish

Structure translations so more languages can be added later.

Use:
- gettext
- `{% translate %}`
- `{% blocktranslate %}`

Do not put English UI strings directly into JavaScript where avoidable. If JavaScript strings are needed, expose translated values safely through Django.

Provide a language switcher in the UI.

## 16. PWA

Implement:
- web app manifest;
- service worker;
- installable application;
- icons;
- offline shell for appropriate pages;
- sensible caching.

Do not cache authenticated/private data in a way that creates a privacy leak.

Downloads themselves do not need to work offline.

PWA must work over HTTPS in production.

## 17. SMTP

Support SMTP configuration through environment variables.

Example settings:
- SMTP host
- SMTP port
- username
- password
- TLS
- SSL
- from address
- timeout

Use Django email backend.

Possible notifications:
- batch completed;
- batch failed;
- shared link created;
- password reset;
- important admin events.

Do not send email for every individual track by default.

Provide an admin "Send test email" function.

## 18. Authentication

Use Django authentication.

Support:
- login;
- logout;
- password reset;
- secure password storage;
- session expiry;
- admin/staff roles.

Optional future support:
- registration;
- OIDC.

Do not add registration unless it is useful to the application requirements.

## 19. API design

Use a small internal JSON API where needed for dynamic UI.

If adding Django REST Framework, keep it focused.

Endpoints should have explicit permission checks.

Do not expose raw database objects.

Use serializers/schemas.

## 20. Rate limiting and abuse protection

Especially important for:
- shared links;
- URL submissions;
- MusicBrainz;
- Navidrome;
- authentication.

Add application-level rate limiting where practical.

Limit:
- URLs per batch;
- playlist size;
- maximum download size if detectable;
- concurrent downloads;
- retry count.

## 21. Filesystem layout

Use a dedicated media volume.

Example:

`/data/music`

Temporary files:

`/data/downloads`

Do not expose temporary download files through Nginx.

Use atomic finalization:
1. download temporary file;
2. validate;
3. hash;
4. metadata processing;
5. duplicate check;
6. move to final path;
7. database commit.

Avoid partially written final music files.

## 22. Metadata and file organization

Make the output path configurable.

Default could be:

`Artist/Album/Track Number - Title.ext`

Sanitize filenames safely.

Prevent:
- path traversal;
- invalid filesystem characters;
- control characters;
- excessively long filenames.

Do not let metadata create arbitrary filesystem paths.

## 23. Cleanup

Implement scheduled cleanup for:
- temporary downloads;
- abandoned jobs;
- stale Navidrome cache;
- expired shared links;
- old logs if appropriate.

Never automatically delete final music files merely because a database entry disappeared.

## 24. Observability

Provide:
- structured application logs;
- worker logs;
- admin-visible job failures;
- health endpoint;
- readiness checks.

Docker healthchecks:
- PostgreSQL;
- Redis;
- web;
- worker if practical.

Add `/health/` and `/ready/`.

## 25. Testing

Write tests before considering the implementation complete.

Minimum coverage:
- model validation;
- permissions;
- shared-link expiry;
- shared-link usage limits;
- URL validation;
- duplicate detection;
- SHA-256 hashing;
- filename sanitization;
- spotDL command construction;
- spotDL output parsing;
- Celery task state transitions;
- cancellation;
- retry behavior;
- Navidrome client;
- MusicBrainz client;
- translations;
- PWA assets;
- SMTP test;
- authentication;
- CSRF/security.

Use mocked external APIs in tests.

Never make normal tests depend on a live Navidrome or MusicBrainz server.

## 26. Docker

Create:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `.env.example`

Prefer a multi-stage build if it materially reduces the final image.

Run Django as a non-root user where practical.

Use a separate worker process.

Do not bake secrets into images.

Provide startup/entrypoint logic that:
- waits for dependencies;
- applies migrations;
- collects static files when appropriate.

Do not run migrations from multiple containers simultaneously.

## 27. Documentation

Create:
- README.md
- installation guide
- configuration guide
- admin guide
- user guide
- backup/restore guide
- development guide
- troubleshooting guide

Document:
- required environment variables;
- volumes;
- database backup;
- music backup;
- Navidrome configuration;
- SMTP;
- MusicBrainz;
- spotDL;
- reverse proxy/HTTPS;
- PWA requirements.

## 28. Backups

Document PostgreSQL backup and restore.

The music library is separate from the database and must be backed up separately.

Do not assume a Docker volume is a backup.

Provide example commands/scripts for:
- PostgreSQL dump;
- restore;
- database integrity checks.

## 29. Security checklist

Before completion verify:

- [ ] DEBUG can be disabled.
- [ ] SECRET_KEY is externalized.
- [ ] Secure cookies work behind HTTPS.
- [ ] CSRF trusted origins are configurable.
- [ ] HSTS can be enabled.
- [ ] Clickjacking protection is enabled.
- [ ] Content type sniffing protection is enabled.
- [ ] User-uploaded/input URLs are validated.
- [ ] Subprocess arguments cannot be injected.
- [ ] Shared links are unguessable.
- [ ] Shared links expire.
- [ ] Shared links are rate limited.
- [ ] Admin endpoints require authentication.
- [ ] Secrets are not returned in API responses.
- [ ] Logs do not contain passwords/tokens.
- [ ] Path traversal is prevented.
- [ ] Temporary files cannot be downloaded by arbitrary users.
- [ ] Dependency versions are pinned/managed.
- [ ] Production deployment uses HTTPS.

## 30. Implementation sequence

Implement in this order:

Phase 1:
- project skeleton;
- Docker;
- PostgreSQL;
- Redis;
- Celery;
- Django auth;
- Bootstrap;
- base layout;
- i18n.

Phase 2:
- DownloadBatch;
- DownloadItem;
- spotDL service;
- Celery download tasks;
- basic UI.

Phase 3:
- SHA-256 hashing;
- duplicate detection;
- TrackFile;
- filesystem management.

Phase 4:
- live download status;
- retry/cancel;
- queue management.

Phase 5:
- Navidrome integration;
- synchronization;
- matching.

Phase 6:
- MusicBrainz integration;
- metadata review/update.

Phase 7:
- shared import links;
- expiry;
- rate limiting.

Phase 8:
- PWA;
- themes;
- language switcher;
- SMTP notifications.

Phase 9:
- admin/settings;
- health checks;
- cleanup;
- backup documentation.

Phase 10:
- security hardening;
- test suite;
- production documentation.

## 31. Definition of done

The project is not complete until:

- `docker compose up -d` starts the stack;
- migrations work from a clean database;
- a user can submit a URL;
- spotDL downloads asynchronously;
- progress is visible;
- completed files are hashed;
- duplicates are detected;
- database records survive restarts;
- playlists work;
- multiple URLs work;
- Navidrome can be synchronized;
- MusicBrainz metadata can be queried;
- shared links work and expire;
- admin settings work;
- English and Finnish UI work;
- theme switching works;
- PWA installation works;
- SMTP test works;
- tests pass;
- README contains production deployment instructions.

## 32. Development behavior for Claude Code

Work iteratively.

Before implementing a major subsystem:
1. inspect the existing project;
2. identify relevant files;
3. propose the smallest coherent change;
4. implement it;
5. run tests;
6. fix failures;
7. update documentation.

Do not rewrite working code unnecessarily.

Prefer maintainable Django conventions over clever abstractions.

When a requirement is ambiguous, choose the simplest secure implementation and document the decision.

Do not stop after scaffolding. Build a functioning end-to-end vertical slice first, then expand it.

At the end, provide:
- changed files;
- migrations;
- test results;
- Docker instructions;
- environment variables;
- known limitations.
