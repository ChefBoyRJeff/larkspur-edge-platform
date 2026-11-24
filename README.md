# Larkspur Edge Platform

**Larkspur Track**: an offline-first shipment tracking system for a (fictional) Larkspur Logistics regional warehouse, running on a 2-node **k3s** Kubernetes cluster of Raspberry Pi 5s, built and operated with production practices: Ansible, GitHub Actions, Argo CD (GitOps), cert-manager, Sealed Secrets, CloudNativePG, Prometheus/Grafana, and Azure for off-site backups and sync.

> **Status:** M0 (repository, plan, standards). See [docs/plan.md](docs/plan.md). Evidence of every verified step is in [docs/evidence](docs/evidence).

| Host | Hardware | Role |
|---|---|---|
| lark-sw01 | NETGEAR GS748Tv3 (managed) | Site switch |
| lark-k3s-01 | Raspberry Pi 5, 8 GB | k3s control plane + workloads |
| lark-k3s-02 | Raspberry Pi 5, 8 GB | k3s worker |

Larkspur Logistics is fictional. All data in this repository is synthetic.
