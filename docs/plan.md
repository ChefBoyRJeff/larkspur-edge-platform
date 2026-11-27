# Plan: Larkspur Edge Platform

Approved: 11-24-2026. Product: **Larkspur Track**. Stack: **Python + FastAPI**, server-rendered pages, **PostgreSQL** (see ADR-0001). Site: Larkspur Labs.

## 1. Scenario
Larkspur Logistics (fictional) opens a regional warehouse. Staff need shipment tracking that **keeps working when the internet is down** and **syncs with the company's Azure environment** when it returns. The on-site platform runs in the warehouse server room: a 2-node k3s cluster on Raspberry Pi 5s behind the lark-sw01 switch.

## 2. Goals
1. A working product: shipments, status workflow, event timeline, roles, dashboards.
2. Production practices: everything as code, CI/CD, security, backups, monitoring, runbooks.
3. Portable: one Helm chart runs on the Pis (arm64) and on AKS (amd64).
4. Rebuildable: the whole site can be recreated from Git.
5. Course-ready: structured to serve as a UNCG master's project foundation.

## 3. Out of scope (v1.0)
| Item | Where it goes |
|---|---|
| SSO (Active Directory / Entra ID) | Phase 2 (app built OIDC-ready) |
| lark-hv01, Active Directory, file server, Azure Arc | Phase 2: corporate IT |
| Pi 4 nodes, Pi-hole DNS, VLANs | Phase 2: site expansion |
| Running on AKS | Separate project, using this chart |
| Highly available control plane | Known limitation (1 control plane) |
| Barcode scanner hardware | Optional later (keyboard-wedge scan page) |

## 4. Architecture (target)
```
 Warehouse staff (browser)
        | HTTPS  track.larkspur.home.arpa  (internal CA certificate)
        v
 k3s cluster: lark-k3s-01 (control plane) + lark-k3s-02 (worker)
   Traefik ingress -> larkspur-track (FastAPI, 2 replicas, non-root) -> CloudNativePG (PostgreSQL)
   outbox worker  -> (internet) -> Azure Storage Queue
   Argo CD <- GitHub (main / PRs); cert-manager; Sealed Secrets; network policies
   Prometheus, Grafana, Alertmanager, blackbox exporter; CloudNativePG backups -> Azure Blob
 GitHub Actions: test -> build arm64/amd64 -> Trivy -> GHCR (versioned) -> PR bumps chart -> Argo CD syncs
```

## 5. Modules
| Module | Scope | Done when | Status |
|---|---|---|---|
| Foundation | Repository, plan, ADR-0001, conventions | Repo structure, .gitignore, .gitattributes, plan approved; k3s cluster running | Done |
| Infrastructure | Ansible: base configuration, SSH hardening, k3s at a pinned version | Second playbook run reports 0 changes; lark-k3s-02 rebuilt from a blank card by the playbook rejoins Ready | Done |
| Platform | Argo CD, cert-manager + internal CA, Sealed Secrets, dev/prod namespaces, RBAC, restricted pod security, default-deny network policies | A test page is served over trusted HTTPS by hostname; default-deny and restricted pod security enforced | Done |
| Application | Larkspur Track: shipments, status workflow (received -> stored -> picked -> shipped), timeline, search, dashboard, Alembic migrations, sign-in, clerk/supervisor/admin roles, audit trail | Invalid transitions rejected; migrations apply cleanly; unit tests pass; argon2 password hashing; per-role permissions tested; every change audited | Done |
| CI/CD | GitHub Actions, arm64 + amd64 images, Trivy, versioned GHCR images, CloudNativePG operator + dev and prod databases, Helm chart, Argo CD dev -> prod by pull request | PRs run lint + tests; merges build both architectures; promotion by PR; rollback by revert works | Done |
| Data | Scheduled CloudNativePG backups to Azure Blob, restore | Azure cost approved first; restore to a new instance works; RPO/RTO targets met | Done: RPO 5 min / RTO 30 min targets; measured WAL in Azure in 2-5 s, restore in 71-81 s (ADR-0008) |
| Integration | Offline-first outbox sync to Azure | With the uplink cut, staff keep working; when it returns, every event is delivered exactly once | Done: at-least-once delivery deduplicated by event_id (effectively once); uplink-cut drill kept and delivered 7 of 7 events, 5 s after the link returned, 0 duplicates (ADR-0009) |
| Operations | Prometheus, Grafana, Alertmanager, app metrics, blackbox page checks (instead of Uptime Kuma, ADR-0007), SLOs, runbooks, failure drills | Alerts fire and resolve; SLOs measured; runbooks cover node loss, database pod loss and a bad release | Done: drills for a bad release (users unaffected; faster rollback, CI tag check, LarkspurReleaseStuck), database loss (rebuilt from Azure in 60 s, nothing lost) and node loss (0 failed prod requests after adding Traefik retries; alert email kept with two Alertmanagers) |
| Release | README + diagram, install guide, ADRs, interview guide, release notes | Tagged v1.0 release | Done: v1.0.0 (docs/releases/v1.0.0.md) |

**Build order:** Foundation -> Infrastructure -> Platform -> Application (core) -> CI/CD -> Application (sign-in) -> Operations (monitoring) -> Data -> Integration -> Operations (drills) -> Release.

## 6. Cost
- Edge site: $0 beyond electricity (about $4-5/month for the switch and Pis).
- Azure (Data and Integration modules only): Blob backups + Storage Queue, estimated under $1/month; priced and approved before creation, with a budget alert.
  - Data (approved 11-25-2026): backup storage in rg-lark-edge-backup, about $0.15/month at most at live East US prices; $5 budget `budget-larkspur-edge-backup`.
  - Integration (approved 11-25-2026): sync queues in rg-lark-edge-integration, well under $0.05/month at live East US prices; $5 budget `budget-larkspur-edge-integration`.

## 7. Risks and limitations
| Risk | Mitigation |
|---|---|
| SD card wear (Postgres, Prometheus) | Short retention, limited writes; USB SSD as the upgrade path |
| Single control plane | Accepted for v1; backups + Ansible allow a full rebuild |
| Data tied to one node | Continuous WAL archiving and nightly backups to Azure; restore drill passed (ADR-0008) |
| Audit log trigger can be dropped by the app's database role (table owner) | Guards against bugs and mistakes; a separate audit role without UPDATE/DELETE rights is the upgrade path (ADR-0006) |
| Sync has no receiver yet; queued messages wait in Azure | Messages never expire; an Azure Function or Logic App reads them in Phase 2 and dedupes by event_id (ADR-0009) |
| No internal DNS yet | hosts-file entries until Pi-hole (Phase 2) |
| Switch firmware end-of-life | Admin access limited to the home network |
| Alerts depend on the cluster: Prometheus runs on one node, and a site-wide power or network loss sends no email | healthchecks.io dead man's switch emails when Alertmanager's Watchdog pings stop (about 5 to 8 minutes); a UPS is the hardware fix |
| Internal root CA trusted by the admin PC (key compromise could impersonate any site) | Key stays in the cluster (cert-manager namespace); trusted for the current Windows user only; removable in certmgr.msc; name constraints considered later |
| New pods have unrestricted network access for up to ~3 s, until kube-router programs their firewall chain (k3s sets kube-router's NetPolDefaultDeny to false and does not expose it) | Measured: blocked by t=3 s on both nodes; the API and kubelet still require authentication; only pinned, scanned images run; Cilium or Calico is the upgrade path |

## 8. Decisions
| Decision | Module | Status |
|---|---|---|
| Application stack | Foundation | Python + FastAPI (ADR-0001) |
| Ansible control node | Infrastructure | WSL2 Ubuntu 26.04 on the admin PC, dedicated SSH key |
| Sign-in, sessions, roles, audit log | Application | Local accounts, Argon2id, database sessions (ADR-0006) |
| Client IP at the ingress | Application | Traefik DaemonSet, externalTrafficPolicy Local (ADR-0005) |
| Backup target, credentials and recovery targets | Data | Azure Blob (Hot LRS) through the Barman Cloud plugin, per-environment SAS, RPO 5 min / RTO 30 min (ADR-0008) |
| Alert channel and SLO targets | Operations | Email to Gmail; prod 99.5% success and 99% within 1 s over 7 days (ADR-0007) |
| Failure handling from the drills | Operations | Argo CD retries with the latest commit; CI checks image tags exist; Alertmanager runs on both nodes; Traefik retries a web pod it cannot reach (2 s connect timeout) |
| Sync target, delivery guarantee and credentials | Integration | Transactional outbox and one sync worker per environment; Azure Storage Queue (one per environment, messages never expire); at-least-once, deduplicated by event_id; send-only SAS per queue (ADR-0009) |

## 9. Working agreement
- Conventional Commits (feat, fix, docs, chore, ci, test, refactor) with the module as scope; one logical change per commit; PR titles in the same style.
- Every PR description ends with a short **Verified** line stating how the change was checked.
- Postmortems only for real outages; operational knowledge goes into runbooks.
- No secrets in Git; synthetic data only; Azure costs approved before anything bills.
- AI assistance is recorded in docs/ai-use-log.md. Once UNCG classes start, UNCG guidance and the instructor's policy apply.
