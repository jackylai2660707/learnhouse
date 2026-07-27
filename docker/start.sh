#!/bin/sh

set -eu

# Set environment variables for proper Python logging
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

# Refuse ambiguous environments. Explicit development mode keeps local HTTP
# installs working; every production start must pass the full gate.
case "${LEARNHOUSE_ENV:-}" in
    production|prod)
        cd /app/api
        uv run python scripts/production_preflight.py
        ;;
    dev|development|test)
        echo "Skipping the production startup gate in explicit non-production mode."
        ;;
    *)
        echo "LEARNHOUSE_ENV must be set to production or an explicit non-production mode."
        exit 1
        ;;
esac

# Wait for database and redis if connection strings point to external services
# (In docker-compose, depends_on handles this, but useful for standalone)
if [ -n "${LEARNHOUSE_SQL_CONNECTION_STRING:-}" ]; then
    DB_HOST=$(echo "$LEARNHOUSE_SQL_CONNECTION_STRING" | sed -n 's/.*@\([^:]*\):\([0-9]*\)\/.*/\1/p')
    if [ -n "$DB_HOST" ] && [ "$DB_HOST" != "localhost" ] && [ "$DB_HOST" != "127.0.0.1" ] && [ "$DB_HOST" != "db" ]; then
        echo "Waiting for external database at $DB_HOST..."
        timeout 30 sh -c 'until nc -z '"$DB_HOST"' 5432; do sleep 1; done' || true
    fi
fi

# All production schema upgrades happen under a PostgreSQL advisory lock before
# services run. Development keeps the prior create_all-compatible behavior.
case "$LEARNHOUSE_ENV" in
    production|prod)
        cd /app/api
        uv run python scripts/production_migrate.py
        ;;
esac

# Start the services
# Use server-wrapper.js for runtime environment variable injection
pm2 start server-wrapper.js --cwd /app/web/apps/web --name learnhouse-web > /dev/null 2>&1
pm2 start uv --cwd /app/api --name learnhouse-api -- run app.py
# Durable PDF builds run outside request workers. Keep exactly one worker per
# application container; PostgreSQL leases + SKIP LOCKED recover interrupted
# work without overlapping jobs.
pm2 start uv --cwd /app/api --name learnhouse-pdf-worker -- run python -m src.workers.pdf_course_build_worker
pm2 start node --cwd /app/collab --name learnhouse-collab -- dist/index.js

# Check if the services are running and log the status
pm2 status

# Start Nginx in the background
nginx -g 'daemon off;' &

# Tail PM2 logs with proper formatting
pm2 logs --raw
