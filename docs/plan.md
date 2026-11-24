# Plan: Larkspur Edge Platform

Plan approved: 11-24-2026. Product: **Larkspur Track**. Stack: **Python + FastAPI**, server-rendered pages, **PostgreSQL**. Site: Larkspur Labs.

## 1. Scenario
Larkspur Logistics (fictional) opens a regional warehouse. Staff need shipment tracking that **keeps working when the internet is down** and **syncs with the company's Azure environment** when it returns. The on-site platform runs in the warehouse server room: a 2-node k3s cluster on Raspberry Pi 5s behind the lark-sw01 switch.

## 2. Goals
1. A working product: shipments, status workflow, event timeline, roles, dashboards.
2. Production practices: everything as code, CI/CD, security, backups, monitoring, documented incidents.
3. Portable: one Helm chart runs on the Pis (arm64) and on AKS (amd64).
4. Rebuildable: the whole site can be recreated from Git.
5. Course-ready: structured to serve as a UNCG master's project foundation.

## 3. Scope

### In scope (v1.0)
| Area | Included |
|---|---|
| App | Shipments (create, search, view); status workflow received -> stored -> picked -> shipped with invalid transitions blocked; per-shipment event timeline; clerk / supervisor / admin roles; audit trail; dashboard |
| Data | PostgreSQL via the CloudNativePG operator; versioned migrations (Alembic) |
| Infrastructure as code | Ansible: blank SD card -> hardened node -> k3s |
| CI | Lint + tests, arm64 + amd64 images, Trivy scan, versioned images in GHCR, Helm lint |
| CD | Argo CD (GitOps): dev and prod namespaces, promotion by pull request |
| Security | Internal CA TLS (cert-manager), Sealed Secrets, RBAC, default-deny network policies, non-root containers, restricted pod security |
| Operations | Prometheus, Grafana, Alertmanager, app metrics, Uptime Kuma status page, SLOs, runbooks |
| Resilience | Postgres backups to Azure Blob; tested restore with measured RPO/RTO |
| Offline-first sync | Outbox pattern: events saved locally, delivered to Azure when the internet returns; tested with a real outage |
| Docs | README + diagram, ADRs, RCAs, runbooks, install guide, interview guide, AI-use log |

### Out of scope (v1.0)
| Item | Where it goes |
|---|---|
| SSO (Active Directory / Entra ID) | Phase 2 (app built OIDC-ready) |
| lark-hv01, Active Directory, file server, Azure Arc | Phase 2: corporate IT |
| Pi 4 nodes, Pi-hole DNS, VLANs | Phase 2: site expansion |
| Running on AKS | Post-capstone Project 3, using this chart |
| Highly available control plane | Documented limitation (1 control plane) |
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


## 5. Milestones
| # | Milestone | Acceptance criteria (evidence required) |
|---|---|---|
| M0 | Repo, plan, standards | Repo, .gitignore, leak check, ADR-0001, AI-use log; Phase 0/1 evidence imported (cluster Ready, self-healing 0 failed requests) |
| M1 | Reproducible nodes (Ansible) | Playbook rebuilds lark-k3s-02 from a blank card and it rejoins Ready; second run reports 0 changes |
| M2 | Platform services | Argo CD, cert-manager + internal CA, Sealed Secrets, dev/prod namespaces; test page over trusted HTTPS by hostname |
| M3 | Larkspur Track v0.1 | Shipments + status workflow work locally; invalid transitions rejected; migrations apply cleanly; unit tests pass |
| M4 | CI pipeline | PR runs lint + tests; merge builds arm64 + amd64 images; Trivy passes or findings documented; versioned push to GHCR |
| M5 | Helm + GitOps deploy | Chart deploys to dev via Argo CD; promotion to prod by PR; rollback by reverting a commit proven |
| M6 | Sign-in and roles | argon2 password hashing; role permissions tested per role; audit trail records every change |
| M7 | Observability | Dashboards (cluster, app, DB); an alert fires and resolves under a controlled failure; status page live; SLOs defined and measured |
| M8 | Backup and restore | Scheduled backups to Azure Blob (cost approved first); restore to a new instance verified; RPO/RTO measured |
| M9 | Offline-first sync | Uplink cut -> staff keep working, outbox grows -> uplink restored -> every event delivered exactly once |
| M10 | Hardening + game day | Default-deny proven; restricted pod security; scripted failures (Pi unplugged, DB pod killed, bad release) each with an RCA |
| M11 | v1.0 release | README + diagram, install guide, runbooks, ADRs, interview guide, release notes, tagged release |

## 6. Cost
- Edge site: $0 beyond electricity (about $4-5/month for the switch and Pis).
- Azure (M8, M9 only): Blob backups + Storage Queue, estimated under $1/month; priced and approved before creation, with a budget alert.

## 7. Risks and limitations
| Risk | Mitigation |
|---|---|
| SD card wear (Postgres, Prometheus) | Short retention, limited writes; USB SSD as documented upgrade |
| Single control plane | Accepted for v1; backups + Ansible allow full rebuild |
| Data tied to one node | Backups + tested restore |
| Home network limits (no internal DNS yet) | hosts-file entries until Pi-hole (Phase 2) |
| Switch firmware end-of-life | Admin access limited to the home network |

## 8. Open decisions (decided at the milestone that needs them)
1. M1: Ansible control node (recommended: WSL2 Ubuntu on the admin PC).
2. M7: alert channel and SLO targets.
3. M9: sync target (recommended: Azure Storage Queue, after pricing).

## 9. Working agreement
- Until UNCG classes start: Claude writes code and blocks; Jeff runs, verifies, and commits through PRs. After classes start: UNCG guidelines and the instructor's policy apply.
- No secrets in chat or Git; synthetic data only; evidence for every claim; corrections appended, not rewritten; costs approved before anything bills.
