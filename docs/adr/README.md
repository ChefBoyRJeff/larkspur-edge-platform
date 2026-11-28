# Architecture decision records

Each record explains one decision: the context, what was decided, the alternatives considered and the consequences, including measured results where a drill tested them. All are accepted.

| ADR | Decision | Date |
|---|---|---|
| [0001](0001-application-stack.md) | Python with FastAPI, server-rendered Jinja2 pages, PostgreSQL, SQLAlchemy and Alembic | 11-24-2026 |
| [0002](0002-local-development-database.md) | Local development on PostgreSQL 18 inside WSL, with separate dev and test databases | 11-24-2026 |
| [0003](0003-container-images-and-supply-chain.md) | Multi-stage, non-root, read-only images built natively for amd64 and arm64, scanned by Trivy; actions pinned to commit SHAs | 11-24-2026 |
| [0004](0004-packaging-and-deployment.md) | One Helm chart, immutable `sha-` tags per environment, releases and rollbacks by pull request, migrations as an Argo CD Sync hook | 11-24-2026 |
| [0005](0005-ingress-client-ip.md) | Traefik as a DaemonSet with `externalTrafficPolicy: Local`, so the app sees real client addresses | 11-24-2026 |
| [0006](0006-sign-in-and-sessions.md) | Local accounts with Argon2id, sessions in PostgreSQL, three roles and an append-only audit log | 11-24-2026 |
| [0007](0007-monitoring-and-alerting.md) | kube-prometheus-stack with blackbox page checks, email alerts and 7-day SLOs | 11-25-2026 |
| [0008](0008-database-backups.md) | WAL archiving and nightly base backups to Azure Blob Storage with per-environment SAS tokens; RPO 5 minutes, RTO 30 minutes | 11-25-2026 |
| [0009](0009-offline-first-sync.md) | Transactional outbox and a sync worker sending to Azure Storage Queue, at least once, with a send-only credential | 11-26-2026 |
| [0010](0010-high-availability.md) | Three k3s servers with embedded etcd, kube-vip for the API address, two database instances, and every failover component on two nodes | 11-27-2026 |
| [0011](0011-site-dns.md) | Pi-hole on lark-dns-01 for the site's names, reached from the admin PC through an NRPT rule | 11-27-2026 |

Later changes that came out of the Operations drills (Argo CD retries with the latest commit, the image tag check in CI, two Alertmanagers, Traefik retries) are recorded in `docs/plan.md`, "Decisions", and in the runbooks they affect. The Phase 2 node-loss drills are recorded in ADR-0010.

## Adding a record
Copy the shape of an existing record (Status, Date, Context, Decision, Alternatives considered, Consequences), number it next in sequence, add it to this table, and merge it in the same pull request as the change it explains.
