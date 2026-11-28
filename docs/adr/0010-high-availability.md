# ADR-0010: High availability on three Raspberry Pis

- **Status:** Accepted
- **Date:** 11-27-2026

## Context
In v1.0, lark-k3s-01 held the Kubernetes API (k3s with SQLite) and every database volume. Losing it stopped both sites until the node returned or the databases were rebuilt from Azure (ADR-0008). Phase 2 adds two Raspberry Pi 4s (4 GB): one becomes a cluster node, the other runs site DNS (ADR-0011). The goal of this module: losing any one node costs at most a couple of minutes of downtime, without anyone acting, and the right people are told.

The nodes boot from SD cards. There is no shared or replicated storage, no load balancer and no budget for new hardware in this module.

## Decision
- **Three k3s servers with embedded etcd:** lark-k3s-01 and lark-k3s-02 (Pi 5) and lark-k3s-03 (Pi 4). etcd needs a majority, so the cluster survives losing one server. lark-k3s-01's SQLite datastore was converted in place (a copy is kept on the node); lark-k3s-02 was promoted from agent with `promote-to-server.yml`. Runbook: `docs/runbooks/control-plane.md`.
- **One API address, 192.168.1.50, held by kube-vip** (ARP, leader election, control-plane nodes only). The admin PC's kubeconfig and the site's DNS names point at it, so they keep working when a server is lost. Services are not published by kube-vip: k3s ServiceLB still runs Traefik on every node, so the sites also answer on .50.
- **Two PostgreSQL instances per environment** (CloudNativePG primary and streaming replica) with required anti-affinity on hostname, so each copy is on a different node. No PodDisruptionBudget: a drain fails the primary over instead of blocking.
- **Everything the failover depends on runs twice, on different nodes:** the CloudNativePG operator, the Barman Cloud plugin (the operator cannot reconcile a cluster while it is unreachable), Alertmanager and the blackbox exporter. kube-state-metrics stays at one replica (two would export every series twice) but leaves a lost node after 30 s instead of 5 minutes.
- **etcd timing for SD cards:** `heartbeat-interval=500`, `election-timeout=5000` (10x ratio) on every server, set through `k3s_etcd_args`.
- **Alerts that survive a node loss:** `LarkspurDatabaseDown` fires when no instance reports as the primary (`cnpg_pg_replication_in_recovery == 0`); `LarkspurDatabaseReplicaMissing` fires when one copy is missing for 10 minutes; a healthchecks.io dead man's switch emails when Prometheus or Alertmanager stops (ADR-0007).
- **Proven by drills:** `platform/drills/node-loss-drill.ps1` powers a node off, probes both sites every second, records failover, API address, Prometheus and alert state, and waits for the node to return.

## Alternatives considered
- **Keep one control plane (v1.0):** simplest, but one node's loss stops everything.
- **An external datastore (PostgreSQL through kine) on another machine:** moves the single point of failure to that machine.
- **Replicated block storage (Longhorn):** every write crosses the network to SD cards on other nodes, and its components need memory the Pi 4 does not have. Database replication gives the same protection for the data that matters, at the database level.
- **keepalived or MetalLB for the API address:** keepalived is one more host service to manage outside Kubernetes; MetalLB solves Service addresses, which ServiceLB already covers. kube-vip runs inside the cluster, so nothing extra is installed on the hosts.
- **SSDs now:** the real fix for etcd's disk latency, but a cost; deferred (below).
- **Azure storage for etcd:** every etcd write waits for the disk, and a round trip to Azure is tens of milliseconds, slower than the SD cards; it would also stop the cluster whenever the internet is down, which defeats the site's purpose.

## Consequences
- **Measured (node-loss drills, 11-27-2026, prod `/readyz` every second):**

| Drill | Lost node held | Prod down | Fix that followed |
|---|---|---|---|
| 04:09 PM, lark-k3s-01 | Both primaries, the only operator, Prometheus | 11 min 6 s; no email | Second operator (#84), etcd timing (#85), dead man's switch (#86) |
| 09:46 PM, lark-k3s-02 | Both primaries, the only backup plugin | 7 min 52 s | Second plugin (#87) |
| 10:32 PM, lark-k3s-01 | Both primaries, Prometheus | 1 min 33 s + 36 s gap at 5 min; healthchecks.io email at 6 min | Alerts that survive a node loss (#90), WAL alert fix (#91), replica rejoin runbook (#89) |
| 11:26 PM, lark-k3s-02 | Both primaries | 86 s + 48 s gap at 5 min; Grafana page alert fired and resolved | None needed |

- **etcd on SD cards is the main remaining limit.** At rest etcd already logs requests taking 200-700 ms (expected under 100 ms). When Kubernetes evicts a lost node's pods at the 5-minute mark, the burst of writes still makes k3s restart on the surviving servers, which drops the API for about a minute and the sites for 36-48 s. SSDs for each server's etcd directory (estimated at about $170 for three on 11-27-2026) are the documented upgrade.
- **A rejoining former primary can wait for new WAL.** On an idle database it stays not ready until the primary writes something; a `CHECKPOINT` on the primary ends it (`docs/runbooks/alerts.md`, LarkspurDatabaseReplicaMissing). Seen after one of the drills.
- **Prometheus runs once.** While its node is down there are no metrics or alerts from inside; the dead man's switch reports that. A second Prometheus would add memory and SD-card writes to the Pi 4.
- **Rebuilding lark-k3s-01 from a blank card is not automated:** the role gives the first server `cluster-init`, which would start a new cluster. `docs/runbooks/replace-a-node.md` covers the other servers.
- **The Pi 4 has the least memory** (1.7 GB available of 3.8 GB after the first drill); pods that move to it during a node loss must fit.
