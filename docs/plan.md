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
   Prometheus, Grafana, Alertmanager, Uptime Kuma; CloudNativePG backups -> Azure Blob
 GitHub Actions: test -> build arm64/amd64 -> Trivy -> GHCR (versioned) -> PR bumps chart -> Argo CD syncs
```

## 5. Modules
| Module | Scope | Done when | Status |
|---|---|---|---|
| Foundation | Repository, plan, ADR-0001, conventions | Repo structure, .gitignore, .gitattributes, plan approved; k3s cluster running | Done |
| Infrastructure | Ansible: base configuration, SSH hardening, k3s at a pinned version | Second playbook run reports 0 changes; lark-k3s-02 rebuilt from a blank card by the playbook rejoins Ready | Done |
| Platform | Argo CD, cert-manager + internal CA, Sealed Secrets, dev/prod namespaces, RBAC, restricted pod security, default-deny network policies | A test page is served over trusted HTTPS by hostname; default-deny and restricted pod security enforced | Done |
| Application | Larkspur Track: shipments, status workflow (received -> stored -> picked -> shipped), timeline, search, dashboard, Alembic migrations, sign-in, clerk/supervisor/admin roles, audit trail | Invalid transitions rejected; migrations apply cleanly; unit tests pass; argon2 password hashing; per-role permissions tested; every change audited | In progress (core done) |
| CI/CD | GitHub Actions, arm64 + amd64 images, Trivy, versioned GHCR images, CloudNativePG operator + dev database, Helm chart, Argo CD dev -> prod by pull request | PRs run lint + tests; merges build both architectures; promotion by PR; rollback by revert works | In progress |
| Data | CloudNativePG prod database, scheduled backups to Azure Blob, restore | Azure cost approved first; restore to a new instance works; RPO/RTO targets met | Planned |
| Integration | Offline-first outbox sync to Azure | With the uplink cut, staff keep working; when it returns, every event is delivered exactly once | Planned |
| Operations | Prometheus, Grafana, Alertmanager, app metrics, Uptime Kuma, SLOs, runbooks, failure drills | Alerts fire and resolve; SLOs measured; runbooks cover node loss, database pod loss and a bad release | Planned |
| Release | README + diagram, install guide, ADRs, interview guide, release notes | Tagged v1.0 release | Planned |

**Build order:** Foundation -> Infrastructure -> Platform -> Application (core) -> CI/CD -> Application (sign-in) -> Operations (monitoring) -> Data -> Integration -> Operations (drills) -> Release.

## 6. Cost
- Edge site: $0 beyond electricity (about $4-5/month for the switch and Pis).
- Azure (Data and Integration modules only): Blob backups + Storage Queue, estimated under $1/month; priced and approved before creation, with a budget alert.

## 7. Risks and limitations
| Risk | Mitigation |
|---|---|
| SD card wear (Postgres, Prometheus) | Short retention, limited writes; USB SSD as the upgrade path |
| Single control plane | Accepted for v1; backups + Ansible allow a full rebuild |
| Data tied to one node | Backups + tested restore |
| No internal DNS yet | hosts-file entries until Pi-hole (Phase 2) |
| Switch firmware end-of-life | Admin access limited to the home network |
| Internal root CA trusted by the admin PC (key compromise could impersonate any site) | Key stays in the cluster (cert-manager namespace); trusted for the current Windows user only; removable in certmgr.msc; name constraints considered later |

## 8. Decisions
| Decision | Module | Status |
|---|---|---|
| Application stack | Foundation | Python + FastAPI (ADR-0001) |
| Ansible control node | Infrastructure | WSL2 Ubuntu 26.04 on the admin PC, dedicated SSH key |
| Alert channel and SLO targets | Operations | Open |
| Sync target | Integration | Open (recommended: Azure Storage Queue, after pricing) |

## 9. Working agreement
- Conventional Commits (feat, fix, docs, chore, ci, test, refactor) with the module as scope; one logical change per commit; PR titles in the same style.
- Every PR description ends with a short **Verified** line stating how the change was checked.
- Postmortems only for real outages; operational knowledge goes into runbooks.
- No secrets in Git; synthetic data only; Azure costs approved before anything bills.
- AI assistance is recorded in docs/ai-use-log.md. Once UNCG classes start, UNCG guidance and the instructor's policy apply.
