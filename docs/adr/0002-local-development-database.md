# ADR-0002: Local development database

- **Status:** Accepted
- **Date:** 11-24-2026

## Context
Larkspur Track uses PostgreSQL (ADR-0001). The admin PC has no container runtime. In the cluster, the database will be CloudNativePG running PostgreSQL 18 (Data module). Tests must exercise real migrations and real SQL.

## Decision
Run PostgreSQL 18 from Ubuntu's packages inside WSL, reached from Windows at `127.0.0.1:5432`. A `larkspur` role owns two databases: `larkspur_dev` and `larkspur_test`. `app/scripts/setup-dev-db.sh` installs and creates them and writes the gitignored `app/.env` with a random password.

## Alternatives considered
- **SQLite:** different SQL dialect, types and constraint behavior; hides migration bugs that PostgreSQL would catch.
- **PostgreSQL in Docker or Podman:** no container runtime is installed; a large dependency for one database.
- **PostgreSQL Windows installer:** a system-wide Windows service that diverges from the Linux production environment.
- **A database in the cluster:** depends on the CI/CD and Data modules and ties local development to the cluster.

## Consequences
- Development matches the production major version (18).
- Tests need WSL running; `/readyz` and the migration tests fail clearly when it is not.
- Re-running the setup script rotates the password and rewrites `.env`.
- CI will use a PostgreSQL 18 service container.
