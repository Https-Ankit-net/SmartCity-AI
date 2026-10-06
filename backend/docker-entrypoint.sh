#!/bin/sh
# Wait for the database, apply migrations once, then hand over to the server command.
# Workers run with AUTO_MIGRATE=false so they don't race each other on startup.
set -e

attempt=1
until python -c "from sqlalchemy import create_engine, text; from app.db.database import DATABASE_URL; create_engine(DATABASE_URL).connect().execute(text('SELECT 1'))" 2>/dev/null; do
    if [ "$attempt" -ge 30 ]; then
        echo "Database not reachable after $attempt attempts; giving up." >&2
        exit 1
    fi
    echo "Waiting for database ($attempt/30)..."
    attempt=$((attempt + 1))
    sleep 2
done

if [ "${SKIP_MIGRATIONS:-false}" != "true" ]; then
    python -m app.db.migrate
fi

exec "$@"
