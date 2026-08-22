# syntax=docker/dockerfile:1
#
# Single image shared by the web, worker, and beat services (CLAUDE.md #3) —
# they differ only in the command docker-compose.yml gives them. Multi-stage
# so the final image doesn't carry build toolchains.

FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt requirements/base.txt
RUN pip install --no-cache-dir --prefix=/install -r requirements/base.txt


FROM python:3.11-slim-bookworm AS runtime

# ffmpeg is required by spotDL to transcode/mux downloaded audio.
# unzip is only needed to install Deno below; not needed at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        unzip \
        gettext \
    && rm -rf /var/lib/apt/lists/*

# Deno is required by yt-dlp (spotDL's default YouTube backend) to solve
# YouTube's JS player challenge and obtain a valid PO token — without it,
# YouTube downloads increasingly fail with "HTTP 403: Forbidden" even
# though search/metadata calls succeed (see docs/TROUBLESHOOTING.md).
# Installed system-wide, not via `spotdl --download-deno` (which only
# writes to the app user's per-container config dir and would be lost on
# every rebuild) so every worker replica has it without extra runtime setup.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version

COPY --from=builder /install /usr/local

# yt-dlp (spotDL's YouTube backend) ships frequent point releases to
# counter YouTube's changes, so this deliberately overrides whatever
# (older) version spotDL's own dependency pin would otherwise bring in.
#
# No longer --pre: that was needed for a short window while a fix for a
# broken stable release hadn't shipped yet (see docs/TROUBLESHOOTING.md,
# "Items reach downloading and fail every time with HTTP Error 403" for
# the incident this traces back to) — a newer stable release has since
# landed the fix, so plain -U (latest *stable*) is back to being the
# right, less risky default. If yt-dlp ever regresses like this again
# before a fix reaches stable, reintroduce --pre temporarily rather than
# pinning backwards to an old version.
RUN pip install --no-cache-dir -U "yt-dlp[default]" \
    && yt-dlp --version

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --shell /bin/bash --create-home app

WORKDIR /app

COPY --chown=app:app . .

RUN mkdir -p /data/music /data/downloads /data/static /data/media \
    && chown -R app:app /data /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.base

USER app

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
