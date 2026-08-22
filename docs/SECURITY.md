# Security posture

The application resolves and downloads externally-referenced content and accepts submissions
from anonymous shared-link visitors, so every input is treated as untrusted by design
(CLAUDE.md #1-2, #29's checklist). This describes what's actually implemented and verified, not
a requirements wishlist — see the referenced file for each claim.

## URL handling

Every submitted URL is validated before anything else touches it
(`apps/downloader/validators.py`): scheme restricted to `http`/`https`, hostname resolved and
checked against private/loopback/link-local ranges (SSRF protection), malformed URLs rejected.
This runs identically for the main submission form and the anonymous shared-link page.

## Subprocesses

spotDL is always invoked as an argument array with `shell=False`
(`apps/downloader/services/spotdl.py`) — nothing here ever builds a shell string, so no field
derived from a URL, title, or artist can inject shell syntax regardless of content. Execution
time is bounded (`SPOTDL_DOWNLOAD_TIMEOUT`/`SPOTDL_RESOLVE_TIMEOUT`), concurrency is bounded
(`DOWNLOAD_CONCURRENCY`), and retries are capped (`SPOTDL_MAX_RETRIES`).

## Shared links

Tokens are generated with `secrets.token_urlsafe(32)` and only a SHA-256 hash of the token is
ever stored (`apps/sharing/models.py`) — the raw token exists only in the URL the admin
distributes, never in the database. Both link access and import submissions are rate-limited.
Links expire automatically and enforce a maximum-uses count.

## Filesystem

Every path component derived from track metadata is sanitized before touching the filesystem
(`apps/library/filenames.py`): `../`/absolute paths collapsed, control characters and
filesystem-unsafe characters stripped, reserved device names avoided, length capped. A
collision never overwrites an existing file — it gets a hash-suffixed name instead. Temporary
download files live under `DOWNLOAD_TEMP_ROOT`, never inside a directory nginx serves directly.

## Authentication

Django's built-in authentication and password hashing are used unmodified. Every batch/item/
library/admin view checks that the requesting user owns the resource (or is staff) before
returning anything — never only hiding a button in the UI. Login attempts are rate-limited
(`apps/accounts/tests/test_auth_security.py`).

## Secrets

No SMTP password, API token, or the Django secret key is ever committed — all come from
environment variables (`.env`, gitignored) or the deployment's own secret manager. None are
ever returned in an API/HTML response, including in Django admin (verified for shared-link
tokens specifically, which store only a hash).

## Logging

Application logs never include passwords, session cookies, authentication tokens, or a raw
shared-link token — only hashes/IDs where a shared link needs to be identified in a log line.

## Privacy

A logged-in user's own batches/library view only ever shows their own data (or, for staff, an
explicit administrative view) — enforced at the queryset level, not just hidden in templates.
A shared-link visitor sees only that link's own submission status, never other users' batches,
admin pages, or site-wide library contents.

## Verified checklist (CLAUDE.md #29)

- [x] `DEBUG` can be disabled (`DJANGO_DEBUG`, defaults false in `.env.example`'s production
      guidance).
- [x] `SECRET_KEY` externalized (`DJANGO_SECRET_KEY`).
- [x] Secure cookies configurable for HTTPS (`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`).
- [x] CSRF trusted origins configurable (`DJANGO_CSRF_TRUSTED_ORIGINS`).
- [x] HSTS configurable (`SECURE_HSTS_SECONDS`).
- [x] Clickjacking protection (Django's default `X-Frame-Options` middleware, on).
- [x] Content-type sniffing protection (Django's default `SecurityMiddleware`, on).
- [x] User-supplied URLs validated (SSRF, scheme, malformed input).
- [x] Subprocess arguments cannot be injected (`shell=False`, argument arrays throughout).
- [x] Shared links unguessable (`secrets.token_urlsafe(32)`) and expire automatically.
- [x] Shared links rate-limited.
- [x] Admin endpoints require authentication (`@staff_member_required` / Django admin).
- [x] Secrets never returned in API/HTML responses.
- [x] Logs contain no passwords/tokens.
- [x] Path traversal prevented (`apps/library/filenames.py`).
- [x] Temporary files not downloadable by arbitrary users (outside nginx's served paths).
- [x] Dependency versions pinned (`requirements/base.txt`; spotDL pinned exactly, see
      `docs/SPOTDL_VERIFICATION.md`).
- [ ] Production deployment uses HTTPS — this is a deployment-time responsibility (a reverse
      proxy/TLS terminator in front of `nginx`), not something the application enforces itself;
      see `docs/INSTALLATION.md`.
