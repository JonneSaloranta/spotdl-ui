# Test plan

422 tests as of 1.0.0, organized one `tests/` package per app (`apps/<app>/tests/test_*.py`).
Run with `pytest` (see `docs/DEVELOPMENT.md`). External services (spotDL subprocess, MusicBrainz's
HTTP API, SMTP) are always mocked — no test depends on network access or a live third-party
server (CLAUDE.md #25).

## Coverage by area

- **URL/input validation** — `downloader/test_validators.py` (SSRF protection, allowed hosts,
  malformed URLs, per-batch/per-submission limits).
- **spotDL integration** — `downloader/test_spotdl_service.py` (argument construction, output
  parsing, process control/timeout/cancellation), `downloader/test_tasks.py` (resolve/download
  task flow, retry, provider tiering, redelivery/idempotency on redelivery).
- **Duplicate detection & filesystem** — `library/test_services.py` (exact/probable/
  source-identifier duplicates, auto-replace, atomic move, exact-duplicate cleanup),
  `library/test_hashing.py`, `library/test_filenames.py`, `library/test_normalization.py`,
  `library/test_scan.py` (filesystem scan for manually-added files).
- **MusicBrainz** — `musicbrainz/test_client.py` (rate limiting, caching, retry/backoff),
  `musicbrainz/test_enrichment.py` (auto-enrich decision logic), `musicbrainz/test_tasks.py`
  (post-download + background sweep), `musicbrainz/test_views.py` (manual review/apply flow).
- **Sharing** — `sharing/test_models.py` (expiration, usage limits), `sharing/test_views.py`
  (anonymous submission, rate limiting), `sharing/test_admin.py`.
- **Core/platform** — `core/test_csrf.py`, `core/test_ratelimit.py`, `core/test_accessibility.py`,
  `core/test_pwa.py`, `core/test_translations.py` (every UI string round-trips through Finnish),
  `core/test_cleanup.py` (abandoned item/batch detection), `core/test_notifications.py`,
  `core/test_settings_view.py`.
- **Authentication** — `accounts/test_auth_security.py` (login rate limiting, session behavior),
  `accounts/test_profile.py` (theme/language persistence).
- **Permissions** — cross-cutting: every view test module checks unauthorized/cross-user access
  is rejected (not a separate file).

## What's deliberately not covered by automated tests

- Live spotDL/MusicBrainz behavior against the real internet — verified manually instead and
  recorded in `docs/SPOTDL_VERIFICATION.md` (spotDL command syntax/output format) and via direct
  reproduction during development (see `docs/TROUBLESHOOTING.md` for specific incidents).
- Visual/rendering correctness (no browser-based UI test suite) — checked manually against
  Bootstrap's own components.
