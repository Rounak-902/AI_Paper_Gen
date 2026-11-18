#!/bin/sh
set -eu

if [ -z "${PAPERGEN_APP_DB_PASSWORD:-}" ]; then
  echo "PAPERGEN_APP_DB_PASSWORD is required" >&2
  exit 1
fi

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 <<'SQL'
\getenv app_password PAPERGEN_APP_DB_PASSWORD

SELECT 'CREATE ROLE app_user LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user')\gexec

ALTER ROLE app_user
  LOGIN
  NOSUPERUSER
  NOCREATEDB
  NOCREATEROLE
  NOINHERIT
  NOREPLICATION
  NOBYPASSRLS
  PASSWORD :'app_password';
SQL
