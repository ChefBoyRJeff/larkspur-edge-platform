#!/usr/bin/env bash
# Set up local PostgreSQL development and test databases in WSL (Ubuntu).
# Safe to re-run: rotates the larkspur role password and rewrites app/.env (gitignored).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$APP_DIR/.env"

if ! command -v psql >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y postgresql
fi
sudo service postgresql start

PW="$(openssl rand -hex 24)"
(cd / && sudo -u postgres psql -q -v ON_ERROR_STOP=1 -v pw="$PW" <<'SQL'
SELECT format('CREATE ROLE larkspur LOGIN PASSWORD %L', :'pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'larkspur') \gexec
ALTER ROLE larkspur PASSWORD :'pw';
SELECT 'CREATE DATABASE larkspur_dev OWNER larkspur'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'larkspur_dev') \gexec
SELECT 'CREATE DATABASE larkspur_test OWNER larkspur'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'larkspur_test') \gexec
SQL
)

umask 077
cat > "$ENV_FILE" <<EOF
# Local development settings, written by scripts/setup-dev-db.sh. Never commit this file.
DATABASE_URL=postgresql+psycopg://larkspur:${PW}@127.0.0.1:5432/larkspur_dev
TEST_DATABASE_URL=postgresql+psycopg://larkspur:${PW}@127.0.0.1:5432/larkspur_test
# The development server runs over plain HTTP, where browsers drop Secure cookies.
SESSION_COOKIE_SECURE=false
EOF

echo "PostgreSQL $(psql --version | awk '{print $3}') ready: larkspur_dev and larkspur_test."
echo "Wrote $ENV_FILE (password not shown)."
