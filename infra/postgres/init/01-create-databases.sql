-- Fresh-volume local bootstrap. Alembic remains the schema source of truth.
SELECT 'CREATE DATABASE papergen_test'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'papergen_test')\gexec

\connect papergen
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

\connect papergen_test
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

COMMENT ON DATABASE papergen IS 'PaperGen local development database';
COMMENT ON DATABASE papergen_test IS 'PaperGen automated test database - contents are disposable';
