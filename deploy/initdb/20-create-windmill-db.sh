#!/bin/bash
# Create a dedicated database for Windmill inside the SHARED Postgres instance.
#
# Postgres runs files in /docker-entrypoint-initdb.d/ exactly once, on first
# initialization (when the data volume is empty). Dograh keeps using the default
# "postgres" database; Windmill gets its own "${WINDMILL_DB_NAME}" database on the
# same server, so there is only one Postgres container. Windmill creates its own
# tables/schema on first boot; it just needs the database to already exist.
set -e

DB="${WINDMILL_DB_NAME:-windmill}"

# Idempotent: only create if it does not already exist.
if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      -c "CREATE DATABASE \"$DB\";"
  echo "initdb: created database '$DB' for Windmill"
else
  echo "initdb: database '$DB' already exists, skipping"
fi
