#!/bin/sh
# Waits for dependencies, then optionally migrates/collects static before
# handing off to the real command (CLAUDE.md #26). Migrations only run when
# RUN_MIGRATIONS=true (set for the `web` service only in docker-compose.yml)
# so multiple containers never race to apply them concurrently.
set -e

wait_for() {
  host="$1"; port="$2"; label="$3"
  echo "Waiting for $label ($host:$port)..."
  until python -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(('$host', $port))
except OSError:
    sys.exit(1)
"; do
    sleep 1
  done
  echo "$label is up."
}

wait_for "${POSTGRES_HOST:-db}" "${POSTGRES_PORT:-5432}" "PostgreSQL"

redis_host=$(python -c "import os,urllib.parse as u; print(u.urlparse(os.environ.get('REDIS_URL','redis://redis:6379/0')).hostname)")
redis_port=$(python -c "import os,urllib.parse as u; print(u.urlparse(os.environ.get('REDIS_URL','redis://redis:6379/0')).port or 6379)")
wait_for "$redis_host" "$redis_port" "Redis"

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
  echo "Applying database migrations..."
  python manage.py migrate --noinput
fi

if [ "${COLLECT_STATIC:-false}" = "true" ]; then
  echo "Collecting static files..."
  python manage.py collectstatic --noinput
fi

if [ "${SCAN_LIBRARY_ON_STARTUP:-false}" = "true" ]; then
  echo "Queuing a library scan..."
  # Queued on Celery, not run inline: MUSIC_ROOT could hold thousands of
  # files, and startup here must not block on however long hashing all
  # of them takes (CLAUDE.md #4). Quick sync only (add-only) — see
  # queue_library_scan's own help text for why a full sync never runs
  # unattended on boot.
  python manage.py queue_library_scan
fi

exec "$@"
