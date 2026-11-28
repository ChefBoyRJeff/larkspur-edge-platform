# Plan: Larkspur Edge Platform

Approved: 11-24-2026. Product: **Larkspur Track**. Stack: **Python + FastAPI**, server-rendered pages, **PostgreSQL** (see ADR-0001). Site: Larkspur Labs.

## 1. Scenario
Larkspur Logistics (fictional) opens a regional warehouse. Staff need shipment tracking that **keeps working when the internet is down** and **syncs with the company's Azure environment** when it returns. The on-site platform runs in the warehouse server room behind the lark-sw01 switch: a 2-node k3s cluster on Raspberry Pi 5s in v1.0, three k3s servers (two Pi 5s and a Pi 4) plus a Pi 4 for site DNS since Phase 2.

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
| Pi 4 nodes, Pi-hole DNS | Phase 2, Module 1 (done) |
| VLANs | Later |
| Running on AKS | Separate project, using this chart |
| Highly available control plane | Phase 2, Module 1 (done, ADR-0010) |
| Barcode scanner hardware | Optional later (keyboard-wedge scan page) |

## 4. Architecture (target)
```
 Warehouse staff (browser)
        | HTTPS  track.larkspur.home.arpa  (internal CA certificate)
        v
 k3s cluster: lark-k3s-01, -02, -03 (servers, embedded etcd; API at 192.168.1.50 via kube-vip); DNS: Pi-hole on lark-dns-01
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

### Phase 2 (after v1.0)
| Module | Scope | Done when | Status |
|---|---|---|---|
| 1. High availability and site DNS | Two Pi 4s; three k3s servers with embedded etcd; kube-vip; two database instances; failover components on two nodes; Pi-hole on lark-dns-01 | Losing any one node needs no action and the sites return within minutes; alerts still arrive; names resolve without the hosts file | Done: node-loss drills took prod from 11 min 6 s down to about 90 s, plus a 36-48 s gap from etcd on SD cards (ADR-0010); Pi-hole and an NRPT rule replace the hosts file (ADR-0011) |
| 2. Corporate IT | lark-hv01 (Dell 3620) as a Hyper-V host with a domain controller VM; Pi-hole forwards the domain | Domain joined from the admin PC; names resolve both ways | Planned |
| 3. Identity | Entra Connect; OIDC sign-in for Larkspur Track | Users sign in with their directory account; roles map from groups | Planned |
| 4. Queue receiver | Azure Function reading the sync queues, deduplicating by event_id | Every event processed once; cost approved first | Planned |
| 5. Release | Documents, drills and interview material for v2.0 | Tagged v2.0 | Planned |

## 6. Cost
- Edge site: $0 beyond electricity (about $4-5/month for the switch and Pis).
- Azure (Data and Integration modules only): Blob backups + Storage Queue, estimated under $1/month; priced and approved before creation, with a budget alert.
  - Data (approved 11-25-2026): backup storage in rg-lark-edge-backup, about $0.15/month at most at live East US prices; $5 budget `budget-larkspur-edge-backup`.
  - Integration (approved 11-25-2026): sync queues in rg-lark-edge-integration, well under $0.05/month at live East US prices; $5 budget `budget-larkspur-edge-integration`.

## 7. Risks and limitations
| Risk | Mitigation |
|---|---|
| SD card wear (Postgres, Prometheus, etcd) | Short retention, limited writes; SSDs as the upgrade path |
| etcd on SD cards is slow: the eviction burst 5 minutes after a node loss makes k3s restart on the other servers (API about 1 minute, sites 36-48 s) | Longer etcd timeouts (ADR-0010); SSDs for each server (estimated about $170, not approved) |
| Losing two nodes at once | etcd loses quorum and a database can lose both copies; databases come back from Azure (install guide, step 8) |
| Data on local disks | Two instances per database on different nodes (ADR-0010); continuous WAL archiving and nightly backups to Azure; restore drill passed (ADR-0008) |
| Audit log trigger can be dropped by the app's database role (table owner) | Guards against bugs and mistakes; a separate audit role without UPDATE/DELETE rights is the upgrade path (ADR-0006) |
| Sync has no receiver yet; queued messages wait in Azure | Messages never expire; an Azure Function or Logic App reads them in Phase 2 and dedupes by event_id (ADR-0009) |
| One DNS server (lark-dns-01) | Only the site's names stop resolving if it is down; the sites keep running (ADR-0011) |
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
| Failure handling from the drills | Operations | Argo CD retries with the latest commit; CI checks image tags exist; Alertmanager runs on two nodes; Traefik retries a web pod it cannot reach (2 s connect timeout) |
| High availability | Phase 2, Module 1 | Three k3s servers with embedded etcd, kube-vip API address, two database instances, operator, backup plugin, Alertmanager and page checks on two nodes, alerts that survive a node loss, dead man's switch (ADR-0010) |
| Site DNS | Phase 2, Module 1 | Pi-hole on lark-dns-01 configured by Ansible; the admin PC reaches it through an NRPT rule (ADR-0011) |
| Sync target, delivery guarantee and credentials | Integration | Transactional outbox and one sync worker per environment; Azure Storage Queue (one per environment, messages never expire); at-least-once, deduplicated by event_id; send-only SAS per queue (ADR-0009) |

## 9. Working agreement
- Conventional Commits (feat, fix, docs, chore, ci, test, refactor) with the module as scope; one logical change per commit; PR titles in the same style.
- Every PR description ends with a short **Verified** line stating how the change was checked.
- Postmortems only for real outages; operational knowledge goes into runbooks.
- No secrets in Git; synthetic data only; Azure costs approved before anything bills.
- AI assistance is recorded in docs/ai-use-log.md. Once UNCG classes start, UNCG guidance and the instructor's policy apply.
