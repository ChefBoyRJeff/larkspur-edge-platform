# ADR-0006: Sign-in, sessions, roles and the audit log

- **Status:** Accepted
- **Date:** 11-24-2026

## Context
Larkspur Track must know who is using it: shipping needs a supervisor, user management needs an admin, and every change must be traceable to a person and an address. Active Directory and Entra ID single sign-on are Phase 2, and the site must keep working when the internet is down, so v1 needs its own accounts.

## Decision
- **Local accounts** with passwords hashed by **Argon2id** (argon2-cffi) at the OWASP minimum cost (19 MiB, 2 iterations, 1 thread), chosen because every sign-in hashes once and a web pod is limited to 256 MiB on a Raspberry Pi. Each hash records its parameters, so a higher cost is applied at the next sign-in. Passwords need at least 12 characters.
- **Sessions stored in PostgreSQL** (`user_sessions`): the cookie holds a random token and the table holds only its SHA-256. A session ends after 8 hours, or 60 minutes without a page view. Signing out, deactivating the user or resetting their password ends their sessions at once.
- **Cookie:** `__Host-larkspur_session`, HttpOnly, Secure, SameSite=Lax. Local development over HTTP sets `SESSION_COOKIE_SECURE=false`, which drops the prefix and the Secure flag.
- **CSRF protection:** every POST carries the session's synchronizer token and must pass a same-origin check (`Sec-Fetch-Site`, `Origin`). The sign-in form, which has no session yet, gets the same-origin check.
- **Brute force:** five wrong passwords lock the account for 15 minutes. The error message never says whether the username exists, and an unknown username is checked against a dummy hash so it takes as long as a real one.
- **Roles are hierarchical:** clerk < supervisor < admin. Clerks receive shipments and move them up to picked; shipping and the audit log need a supervisor; user management needs an admin. An admin cannot demote or deactivate themselves, so an active admin always remains.
- **Audit log** (`audit_log`): sign-ins, failed sign-ins, sign-outs, shipment changes and user administration, each with the actor, the client IP (real since ADR-0005) and a short detail, written in the same transaction as the change. A trigger rejects UPDATE, DELETE and TRUNCATE. Passwords and tokens are never recorded.
- **The first admin** of each environment is created inside a pod with `larkspur-track-admin create-user`, which asks for the password at a prompt, so it never appears in Git, manifests or shell history.

## Alternatives considered
- **Signed cookie sessions (no table):** no database lookup per request, but a session cannot be ended before it expires, so sign-out and deactivation would not take effect at once.
- **An identity provider now (Keycloak, Entra ID):** single sign-on, but another service to run on the Pis, or a dependency on the internet. The app keeps users and roles in its own tables so OIDC can be added in Phase 2.
- **bcrypt:** well supported, but Argon2id is the OWASP first choice and is memory-hard, which makes GPU cracking more expensive.
- **A dedicated audit database role without UPDATE and DELETE rights:** stronger than a trigger, but needs a second database role and connection; noted below as an improvement.

## Consequences
- Every page except `/healthz` and `/readyz` requires sign-in, and each request reads the session from the database (one indexed lookup; the last-seen time is written at most once a minute).
- Releases before `sha-e5f376a` have no sign-in: rolling back past it turns sign-in off, although the schema stays compatible.
- The table owner (the app's database role) could still drop the audit trigger. The trigger guards against bugs and mistakes, not against someone with that access.
- Lost admin passwords are recovered by creating another admin with the command and resetting the first one on the Users page (see `docs/runbooks/user-administration.md`).
