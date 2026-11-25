# Runbook: Local development

How to set up, run and test Larkspur Track on the admin PC. The database decision is in ADR-0002.

## One-time setup
1. **WSL (Ubuntu):** from the repo root, run `bash app/scripts/setup-dev-db.sh`. It installs PostgreSQL 18 if needed, creates the `larkspur` role with the `larkspur_dev` and `larkspur_test` databases, and writes `app/.env`, including `SESSION_COOKIE_SECURE=false` because the development server uses plain HTTP. It asks for your WSL sudo password.
2. **PowerShell:** in `app/`, run `uv run alembic upgrade head` to bring `larkspur_dev` to the latest schema.

## Daily use (PowerShell, in `app/`)
| Task | Command |
|---|---|
| Run the app (reloads on save) | `uv run larkspur-track`, then open http://127.0.0.1:8000 and sign in with a user you created |
| Run the tests | `uv run pytest` |
| Format and lint | `uv run ruff format .` and `uv run ruff check .` |
| Apply migrations | `uv run alembic upgrade head` |
| Create a migration | `uv run alembic revision --autogenerate -m "describe the change"`, then review the generated file |
| Show the current revision | `uv run alembic current` |
| Create a user (asks for the password) | `uv run larkspur-track-admin create-user --username <name> --display-name "<Full Name>" --role admin` (roles: clerk, supervisor, admin) |

PostgreSQL runs only while WSL is running. If WSL was shut down, open Ubuntu once, or run `wsl -d Ubuntu-26.04 -- sudo service postgresql start`.

## Reset the development database
Run in WSL: `sudo -u postgres dropdb larkspur_dev` and `sudo -u postgres createdb -O larkspur larkspur_dev`. Then run `uv run alembic upgrade head` in PowerShell.

## Troubleshooting
| Symptom | Likely cause | Fix |
|---|---|---|
| `/readyz` returns 503, or tests say "connection refused" | WSL or PostgreSQL is not running | Start Ubuntu, or `sudo service postgresql start` |
| `password authentication failed` | `.env` is out of date | Re-run `bash app/scripts/setup-dev-db.sh` (rotates the password and rewrites `.env`) |
| `TEST_DATABASE_URL is not set` | No `app/.env` | Run the setup script |
| Signing in locally returns to the sign-in page | The browser dropped the Secure session cookie over HTTP | Add `SESSION_COOKIE_SECURE=false` to `app/.env` (the setup script writes it) and restart the app |
| A migration test fails after a model change | The migration does not match the model, or downgrade is incomplete | Fix the migration; every migration must upgrade and downgrade cleanly |
