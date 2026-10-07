#!/bin/bash
# Create a dedicated database for LiteLLM inside the SHARED Postgres instance.
#
# Postgres runs files in /docker-entrypoint-initdb.d/ exactly once, on first
# initialization (when the data volume is empty). Dograh keeps using the default
# "postgres" database; LiteLLM gets its own "${LITELLM_DB_NAME}" database on the
# same server, so there is only one Postgres container.
set -e

DB="${LITELLM_DB_NAME:-litellm}"

# Idempotent: only create if it does not already exist.
if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
      -c "CREATE DATABASE \"$DB\";"
  echo "initdb: created database '$DB' for LiteLLM"
else
  echo "initdb: database '$DB' already exists, skipping"
fi
