# Larkspur Edge Platform

**Larkspur Track**: an offline-first shipment tracking system for a (fictional) Larkspur Logistics regional warehouse, running on a 2-node **k3s** Kubernetes cluster of Raspberry Pi 5s, built and operated with production practices: Ansible, GitHub Actions, Argo CD (GitOps), cert-manager, Sealed Secrets, CloudNativePG, Prometheus/Grafana, and Azure for off-site backups and sync.

> **Status:** Foundation, Infrastructure and Platform done; Application core done; CI/CD next. See [docs/plan.md](docs/plan.md).

| Host | Hardware | Role |
|---|---|---|
| lark-sw01 | NETGEAR GS748Tv3 (managed) | Site switch |
| lark-k3s-01 | Raspberry Pi 5, 8 GB | k3s control plane + workloads |
| lark-k3s-02 | Raspberry Pi 5, 8 GB | k3s worker |

## Architecture

```mermaid
flowchart LR
  subgraph PC["Admin PC (Windows 11)"]
    dev["VS Code, Git, kubectl, Helm"]
    wsl["WSL: Ansible"]
    browser["Browser<br/>trusts Larkspur Labs Root CA"]
  end

  gh[("GitHub<br/>larkspur-edge-platform")]

  subgraph SITE["Home network, lark-sw01 switch"]
    subgraph NODES["Raspberry Pi 5 nodes"]
      n1["lark-k3s-01<br/>control plane + workloads"]
      n2["lark-k3s-02<br/>worker"]
    end
    subgraph K3S["k3s cluster"]
      argocd["Argo CD<br/>app-of-apps, self-managed"]
      cm["cert-manager<br/>larkspur-ca issuer"]
      ss["Sealed Secrets"]
      traefik["Traefik ingress<br/>HTTPS only"]
      subgraph DEVNS["larkspur-dev"]
        page["test-page<br/>2 replicas"]
      end
      subgraph PRODNS["larkspur-prod"]
        app["Larkspur Track<br/>(Application module)"]
      end
    end
  end

  dev -->|"pull request + merge"| gh
  gh -->|"desired state"| argocd
  argocd -->|"syncs"| cm
  argocd -->|"syncs"| ss
  argocd -->|"syncs"| page
  argocd -.->|"planned"| app
  wsl -->|"SSH: site.yml, patch.yml"| n1
  wsl -->|"SSH"| n2
  browser -->|"https://test.larkspur.home.arpa"| traefik
  traefik -->|"allowed by NetworkPolicy"| page
  cm -.->|"TLS certificate"| traefik
```

Both app namespaces enforce the `restricted` Pod Security profile, deny all network traffic by default (DNS excepted), and give the `larkspur-developers` group edit access in dev and read-only access in prod.

## Repository layout
| Path | Contents |
|---|---|
| ansible/ | Node configuration: inventory, roles (common, k3s_server, k3s_agent), site.yml (configure), patch.yml (rolling OS updates) |
| app/ | Larkspur Track (FastAPI, Jinja2, PostgreSQL): uv project, source in src/larkspur_track/, tests in tests/ |
| platform/ | GitOps manifests: bootstrap/ (root app), apps/ (Argo CD Applications), argocd/ (Argo CD Helm values), config/ (CA issuers, app namespaces and guardrails, HTTPS test page) |
| docs/ | Plan, architecture decision records, runbooks, AI-use log |

Larkspur Logistics is fictional. All data in this repository is synthetic.
