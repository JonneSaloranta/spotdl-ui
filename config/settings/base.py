"""
Django settings for the spotDL Web UI project.

Every value that varies between environments (secrets, hosts, credentials,
feature toggles) is read from the environment rather than hard-coded, so the
same image can run in development and production. See docs/ENVIRONMENT.md
and .env.example for the full list of supported variables.
"""

from pathlib import Path

from decouple import Csv, config
from django.contrib.messages import constants as message_constants

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read from the repo-root VERSION file rather than hard-coded here, so a
# release only ever needs updating in one place. Shown in the footer
# (apps.core.context_processors.site_settings) and worth having for support/
# troubleshooting — "what's actually deployed" is otherwise not visible
# anywhere in the running app. Falls back to "dev" so a missing file (e.g. a
# stripped-down build context) never breaks startup over something cosmetic.
try:
    APP_VERSION = (BASE_DIR / "VERSION").read_text(encoding="utf-8").strip()
except OSError:
    APP_VERSION = "dev"

# --------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------

SECRET_KEY = config("DJANGO_SECRET_KEY")
DEBUG = config("DJANGO_DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

CSRF_TRUSTED_ORIGINS = config("DJANGO_CSRF_TRUSTED_ORIGINS", default="", cast=Csv())

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    # Third-party
    "django_htmx",
    # Local apps
    "apps.core",
    "apps.accounts",
    "apps.downloader",
    "apps.library",
    "apps.musicbrainz",
    "apps.sharing",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "apps.core.middleware.MaintenanceModeMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.site_settings",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB", default="spotdl_ui"),
        "USER": config("POSTGRES_USER", default="spotdl_ui"),
        "PASSWORD": config("POSTGRES_PASSWORD", default=""),
        "HOST": config("POSTGRES_HOST", default="db"),
        "PORT": config("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": config("POSTGRES_CONN_MAX_AGE", default=60, cast=int),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "auth.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "core:home"

# Maps Django's own message levels to Bootstrap 5.3 "text-bg-*" contextual
# names, so templates/base.html's toast rendering can use message.tags
# directly as the color class — without this, message.tags is just
# Django's own level name ("success", "error", ...), and "error" in
# particular isn't a Bootstrap color name at all ("danger" is).
MESSAGE_TAGS = {
    message_constants.DEBUG: "secondary",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "danger",
}

# --------------------------------------------------------------------------
# Internationalization
# --------------------------------------------------------------------------

LANGUAGE_CODE = config("DJANGO_LANGUAGE_CODE", default="en")
TIME_ZONE = config("DJANGO_TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ("en", "English"),
    ("fi", "Suomi"),
]

LOCALE_PATHS = [BASE_DIR / "locale"]

# --------------------------------------------------------------------------
# Static / media
# --------------------------------------------------------------------------

STATIC_URL = "static/"
STATIC_ROOT = config("STATIC_ROOT", default=str(BASE_DIR / "staticfiles"))
STATICFILES_DIRS = [BASE_DIR / "static"]

# ManifestStaticFilesStorage (cache-busted, hashed filenames) requires
# `collectstatic` to have been run — good for production, just friction in
# local development, where the dev server serves static files directly
# from source. Only pay that cost when DEBUG is off.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
            if not DEBUG
            else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = config("MEDIA_ROOT", default=str(BASE_DIR / "media"))

# Music library and temporary download locations. These are intentionally
# outside the application source tree (see CLAUDE.md design principle #5)
# and are never served directly by the web server.
MUSIC_ROOT = Path(config("MUSIC_ROOT", default="/data/music"))
DOWNLOAD_TEMP_ROOT = Path(config("DOWNLOAD_TEMP_ROOT", default="/data/downloads"))

# How apps.library.views.track_stream() hands a library file to the browser
# for in-page playback:
#   "nginx"  - respond with X-Accel-Redirect and let nginx serve the bytes
#              (docker/nginx.conf's internal /protected-music/ location).
#              Efficient, and nginx handles HTTP Range requests (seeking)
#              natively. This is the docker-compose default/production path.
#   "direct" - stream the file from Django itself (FileResponse). No nginx
#              dependency, so this is what local dev without the nginx
#              container (or any deployment that doesn't put nginx in
#              front) should use.
# Never expose MUSIC_ROOT itself as a raw static-file location (CLAUDE.md
# #11) - both modes go through track_stream()'s own auth + TrackFile
# lookup rather than an arbitrary filesystem path.
MUSIC_STREAMING_BACKEND = config("MUSIC_STREAMING_BACKEND", default="nginx")

# --------------------------------------------------------------------------
# Redis / Celery
# --------------------------------------------------------------------------

REDIS_URL = config("REDIS_URL", default="redis://redis:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

CELERY_BROKER_URL = config("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = config("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = config("CELERY_TASK_TIME_LIMIT", default=60 * 30, cast=int)
CELERY_WORKER_MAX_TASKS_PER_CHILD = 50
CELERY_TIMEZONE = TIME_ZONE

# Without these, Celery's default is to acknowledge (and so permanently
# remove from the Redis queue) a task the moment a worker *starts* it,
# not when it finishes. `docker compose up -d --build` replacing the
# worker container mid-task — a resolve_batch_task/download_item_task
# genuinely in flight, not just an idle worker — then silently loses
# that task forever: nothing ever redelivers it, so the batch/item is
# left stuck in PENDING/RESOLVING/DOWNLOADING indefinitely, with no
# error recorded anywhere (reproduced for real: a batch stuck at 0 items
# forever, from a deploy that landed mid-resolve). resolve_batch_task and
# download_item_task are already written to be safely re-run from
# scratch (see their own docstrings), so the only piece actually missing
# was telling Celery to *do* that redelivery — CLAUDE.md #9/#16.
#
# Trade-off worth knowing: a task that reliably crashes the worker
# process itself (not just raises normally — a genuine segfault/OOM-kill
# scenario) would now be redelivered and could repeat that crash
# indefinitely rather than being dropped. Not a concern here — nothing
# in this codebase's tasks does anything that should crash the
# interpreter itself, only subprocess calls (already timeout-bounded)
# and ordinary Python/Django code.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
# Pairs with acks_late: without this, a worker can prefetch several
# tasks into its own local buffer, all of which would be equally lost
# (not just the one literally executing) if it dies — 1 means a worker
# only ever holds the task it's actually running.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# The other half of the redelivery fix above, specific to Redis as a
# broker: acks_late means a message is only removed from the queue once
# the task finishes, but with Redis, kombu tracks "delivered, not yet
# acked" messages with a timestamp and only actually requeues one once
# it's older than this visibility_timeout — Celery's own default is
# 3600s (one hour). Left at that default, restarting the worker
# container mid-download does eventually recover (nothing is lost), but
# reproduced for real: the stuck item just sits there, doing nothing,
# for up to an hour after the *new* worker comes back up and starts
# polling again — easily read as "doesn't resume at all" rather than
# "resumes, slowly" (CLAUDE.md #16). Set well above the realistic worst
# case for a single download_item_task (bounded by
# SPOTDL_DOWNLOAD_TIMEOUT + finalize overhead, comfortably under 900s),
# so a still-legitimately-running download is never mistaken for
# abandoned and redelivered to a second worker mid-flight.
#
# resolve_batch_task is the one task that can legitimately still run
# longer than this — a batch with several large playlists
# (queue_batch_resolve() scales its own Celery time_limit up
# accordingly, past the global CELERY_TASK_TIME_LIMIT default). A
# genuine crash during an unusually long multi-source resolve can then
# take up to CLEANUP_INTERVAL_SECONDS (default 6h) to be caught by
# cleanup_abandoned_batches's own safety net instead of this. A
# still-running (not crashed) resolve past this many seconds risks a
# wasteful but harmless duplicate redelivery instead —
# resolve_batch_task is idempotent (see its own docstring), so that
# never corrupts data, only repeats some already-done work.
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": config("CELERY_VISIBILITY_TIMEOUT_SECONDS", default=1800, cast=int),
}

CELERY_BEAT_SCHEDULE = {
    # Was 6h by default — reproduced for real that a batch can end up with
    # items genuinely stuck `pending` (queued, but no corresponding Celery
    # message anywhere — not in the Redis queue, not "active"/"reserved" on
    # any worker; likely lost during a worker restart despite acks_late,
    # under circumstances not fully pinned down) with *no* self-healing for
    # up to 6 hours, since cleanup_abandoned_items/cleanup_abandoned_batches
    # (the actual detection logic, unchanged) only run when this task does.
    # Running the check itself far more often doesn't make it more
    # aggressive — an item still only counts as "stuck" once it's older
    # than 2x its own timeout regardless of how often this fires — it just
    # shrinks how long a genuinely stuck item can sit unnoticed.
    "cleanup": {
        "task": "apps.core.tasks.run_cleanup_task",
        "schedule": config("CLEANUP_INTERVAL_SECONDS", default=60 * 15, cast=int),
    },
    # The task itself is a no-op whenever a download batch is active or
    # MusicBrainz is disabled (SiteSettings.musicbrainz_enabled) — see
    # apps.musicbrainz.tasks.musicbrainz_sweep_task. Safe to check often;
    # each real run only processes a small, admin-configurable batch.
    "musicbrainz-sweep": {
        "task": "apps.musicbrainz.tasks.musicbrainz_sweep_task",
        "schedule": config("MUSICBRAINZ_SWEEP_INTERVAL_SECONDS", default=300, cast=int),
    },
}

# --------------------------------------------------------------------------
# spotDL
# --------------------------------------------------------------------------

SPOTDL_EXECUTABLE = config("SPOTDL_EXECUTABLE", default="spotdl")
SPOTDL_VERSION = config("SPOTDL_VERSION", default="4.5.2")
SPOTDL_AUDIO_FORMAT = config("SPOTDL_AUDIO_FORMAT", default="mp3")
SPOTDL_BITRATE = config("SPOTDL_BITRATE", default="320k")
SPOTDL_OUTPUT_TEMPLATE = config(
    "SPOTDL_OUTPUT_TEMPLATE",
    default="{artist}/{album}/{track-number:02d} - {title}.{output-ext}",
)
SPOTDL_CLIENT_ID = config("SPOTDL_CLIENT_ID", default="")
SPOTDL_CLIENT_SECRET = config("SPOTDL_CLIENT_SECRET", default="")

# Provider tiering: try SPOTDL_PRIMARY_AUDIO_PROVIDER for the first
# SPOTDL_PRIMARY_PROVIDER_ATTEMPTS attempts, then fall back to
# SPOTDL_FALLBACK_AUDIO_PROVIDERS for any attempts after that. Each
# "attempt" is a separate spotDL subprocess invocation driven by the
# per-item retry loop (apps.downloader.tasks) — spotDL's own `--audio p1
# p2` fallback only tries each provider once *within* a single
# invocation, it can't retry p1 several times before moving to p2, so
# this tiering happens a level up instead. Empty primary means "let
# spotDL use its own built-in default" for those attempts.
SPOTDL_PRIMARY_AUDIO_PROVIDER = config("SPOTDL_PRIMARY_AUDIO_PROVIDER", default="youtube")
SPOTDL_PRIMARY_PROVIDER_ATTEMPTS = config("SPOTDL_PRIMARY_PROVIDER_ATTEMPTS", default=3, cast=int)
SPOTDL_FALLBACK_AUDIO_PROVIDERS = config("SPOTDL_FALLBACK_AUDIO_PROVIDERS", default="", cast=Csv())

# SOCKS5/HTTP proxy URL (e.g. socks5://host:port), passed to spotDL's
# --proxy. Only lever available when the deployment's own outbound IP is
# itself rate-limited/blocked by the audio provider — see
# docs/TROUBLESHOOTING.md. Empty (default) omits --proxy entirely.
SPOTDL_PROXY = config("SPOTDL_PROXY", default="")
# Total attempts per item = this + 1 (the initial try). Must be greater
# than SPOTDL_PRIMARY_PROVIDER_ATTEMPTS for the fallback tier above to
# ever actually be reached — otherwise an item gives up while still on
# the primary provider.
SPOTDL_MAX_RETRIES = config("SPOTDL_MAX_RETRIES", default=3, cast=int)
SPOTDL_DOWNLOAD_TIMEOUT = config("SPOTDL_DOWNLOAD_TIMEOUT", default=600, cast=int)
# Separate, larger timeout for resolving a source (spotdl.services.spotdl.resolve_source)
# than for downloading a single track. Resolving fetches metadata for every track in
# the source up front, so its running time scales with playlist size, not with one
# track — a real 150-track playlist was clocked at ~624s, just over the old shared
# 600s SPOTDL_DOWNLOAD_TIMEOUT, which raised a spurious SpotDLTimeoutError even though
# spotDL was still working normally (see docs/TROUBLESHOOTING.md). The default here
# leaves comfortable headroom up to MAX_TRACKS_PER_SOURCE below at that same rate.
SPOTDL_RESOLVE_TIMEOUT = config("SPOTDL_RESOLVE_TIMEOUT", default=2400, cast=int)
DOWNLOAD_CONCURRENCY = config("DOWNLOAD_CONCURRENCY", default=2, cast=int)
# Caps how many DownloadItem rows (and therefore Celery tasks) a single
# source URL can produce — without this, one playlist URL could queue an
# unbounded number of tracks (CLAUDE.md #20: "Limit ... playlist size").
MAX_TRACKS_PER_SOURCE = config("MAX_TRACKS_PER_SOURCE", default=500, cast=int)

# --------------------------------------------------------------------------
# MusicBrainz
# --------------------------------------------------------------------------

MUSICBRAINZ_BASE_URL = config("MUSICBRAINZ_BASE_URL", default="https://musicbrainz.org/ws/2")
MUSICBRAINZ_USER_AGENT = config("MUSICBRAINZ_USER_AGENT", default="spotdl-ui/0.1")
MUSICBRAINZ_CONTACT = config("MUSICBRAINZ_CONTACT", default="")
MUSICBRAINZ_RATE_LIMIT = config("MUSICBRAINZ_RATE_LIMIT", default=1.0, cast=float)

# --------------------------------------------------------------------------
# Email / SMTP
# --------------------------------------------------------------------------

EMAIL_BACKEND = config(
    "DJANGO_EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = config("EMAIL_HOST", default="")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_USE_SSL = config("EMAIL_USE_SSL", default=False, cast=bool)
EMAIL_TIMEOUT = config("EMAIL_TIMEOUT", default=10, cast=int)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="spotdl-ui@localhost")

# --------------------------------------------------------------------------
# Shared import links
# --------------------------------------------------------------------------

SHARED_LINK_DEFAULT_TTL_HOURS = config("SHARED_LINK_DEFAULT_TTL_HOURS", default=24, cast=int)
SHARED_LINK_MAX_TTL_HOURS = config("SHARED_LINK_MAX_TTL_HOURS", default=24 * 14, cast=int)
SHARED_LINK_MAX_USES = config("SHARED_LINK_MAX_USES", default=20, cast=int)
SHARED_LINK_MAX_ITEMS_PER_SUBMISSION = config(
    "SHARED_LINK_MAX_ITEMS_PER_SUBMISSION", default=25, cast=int
)
# How long an expired link's row is kept around (for audit/history)
# before scheduled cleanup deletes it (CLAUDE.md #23).
SHARED_LINK_CLEANUP_RETENTION_DAYS = config("SHARED_LINK_CLEANUP_RETENTION_DAYS", default=30, cast=int)

# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=False, cast=bool)
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=False, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=False, cast=bool)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False, cast=bool
)
SECURE_HSTS_PRELOAD = config("SECURE_HSTS_PRELOAD", default=False, cast=bool)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = config("SESSION_COOKIE_AGE", default=60 * 60 * 24 * 14, cast=int)
SESSION_EXPIRE_AT_BROWSER_CLOSE = config(
    "SESSION_EXPIRE_AT_BROWSER_CLOSE", default=False, cast=bool
)
CSRF_COOKIE_HTTPONLY = False  # required so client-side JS frameworks (HTMX) can read it
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if config(
    "DJANGO_BEHIND_PROXY", default=True, cast=bool
) else None

# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": config("DJANGO_LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "apps": {"handlers": ["console"], "level": config("APP_LOG_LEVEL", default="INFO")},
    },
}
