# Environment variables

Every environment variable the application reads, with its default and what it's for, is
documented in **`docs/CONFIGURATION.md`** and the annotated **`.env.example`** — those two are
the actual, kept-current source of truth (this file used to duplicate a partial list here too,
which drifted out of date; not repeated).

Quick map of where to look for what:

| Area | See |
|---|---|
| Django core (secret key, hosts, debug, i18n, timezone) | `docs/CONFIGURATION.md` → "Required, no safe default" / "Security-relevant" |
| PostgreSQL / Redis | `.env.example` (`--- PostgreSQL ---`, `--- Redis / Celery ---`) |
| spotDL, output format, provider tiering | `docs/CONFIGURATION.md` → "spotDL" |
| Music/download storage paths | `docs/CONFIGURATION.md` → "Storage paths" |
| MusicBrainz | `docs/CONFIGURATION.md` → "MusicBrainz" |
| SMTP | `docs/CONFIGURATION.md` → "SMTP" |
| Shared import links | `docs/CONFIGURATION.md` → "Shared links" |
| Scheduled cleanup | `docs/CONFIGURATION.md` → "Cleanup" |
| Production security (HTTPS, HSTS, secure cookies) | `docs/CONFIGURATION.md` → "Security-relevant" |

Admin-editable settings that are *not* environment variables (change live, no redeploy needed)
live on `/settings/` instead — see `docs/ADMIN_GUIDE.md`'s "Site settings" section.
