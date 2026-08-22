# Installation guide

## Requirements

- Docker and Docker Compose v2 (`docker compose`, not the standalone `docker-compose`).
- A host with outbound internet access (Spotify, the configured audio provider, and
  optionally MusicBrainz).
- A volume/disk large enough for your music library and in-progress downloads
  (`library_data`) — this is separate from the database and grows with every download.

No local Python/PostgreSQL/Redis installation is required — everything runs in containers.

## Steps

1. Clone the repository and enter it.

2. Copy the environment template and fill it in:

   ```bash
   cp .env.example .env
   ```

   At minimum, set:
   - `DJANGO_SECRET_KEY` — generate one, e.g. `python3 -c "import secrets; print(secrets.token_urlsafe(50))"`.
   - `POSTGRES_PASSWORD` — any strong password; it never leaves the Docker network.
   - `DJANGO_ALLOWED_HOSTS` — the hostname(s) you'll access the site through.

   See `docs/CONFIGURATION.md` for every variable and what it controls.

3. Build and start the stack:

   ```bash
   docker compose up -d --build
   ```

   This starts `db` (PostgreSQL) and `redis` first, waits for both to report healthy, then
   starts `web` (which applies migrations and collects static files automatically — see
   `docker/entrypoint.sh`), then `worker`, `beat`, and `nginx`.

4. Watch it come up:

   ```bash
   docker compose ps
   docker compose logs -f web
   ```

   All services should reach `healthy`. If `web` doesn't, check `docker compose logs web` —
   the most common cause is `POSTGRES_PASSWORD` not matching between the `db` service and the
   `web`/`worker`/`beat` services (they all read it from the same `.env`, so this is usually a
   copy-paste mismatch rather than a real bug).

5. Create an administrator account:

   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

6. Visit `http://<host>:${HTTP_PORT:-8000}/`, log in, and confirm `/health/` and `/ready/`
   both return `200`.

## Reverse proxy / HTTPS

`docker-compose.yml` includes an `nginx` service that terminates plain HTTP on
`${HTTP_PORT:-8000}` and proxies to `web`. For a real deployment, put this behind a
TLS-terminating reverse proxy (another nginx, Caddy, Traefik, a cloud load balancer — whatever
you already operate) and set:

```
DJANGO_BEHIND_PROXY=true
SECURE_SSL_REDIRECT=true
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
SECURE_HSTS_SECONDS=31536000
DJANGO_CSRF_TRUSTED_ORIGINS=https://your-actual-domain.example
```

`DJANGO_BEHIND_PROXY=true` makes Django trust the `X-Forwarded-Proto` header from your proxy —
only set it if your proxy actually sets that header and nothing untrusted can reach the `web`
container directly.

## Upgrading

```bash
git pull
docker compose up -d --build
```

The `web` container applies any new migrations automatically on start. `worker`/`beat` don't
run migrations themselves (CLAUDE.md #26: never run migrations from multiple containers), so
`web` should come up first — `docker compose up -d --build` respects the `depends_on` order
already declared in `docker-compose.yml`, so this happens automatically.
