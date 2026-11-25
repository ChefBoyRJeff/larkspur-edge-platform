# ADR-0005: Preserve the client IP address at the ingress

- **Status:** Accepted
- **Date:** 11-24-2026

## Context
The audit trail must record which address a change came from. Traefik, the k3s ingress controller, ran as a single pod on lark-k3s-02 behind a LoadBalancer Service with `externalTrafficPolicy: Cluster`. Clients reach the cluster at lark-k3s-01 (192.168.1.199), so kube-proxy forwarded each connection to the other node and replaced its source address on the way. Traefik and every app saw `X-Forwarded-For: 10.42.0.0` instead of the client's address.

## Decision
Customize the bundled Traefik with a `HelmChartConfig` (`platform/config/traefik/helmchartconfig.yaml`, synced by the `platform-config` Argo CD application):
- **`deployment.kind: DaemonSet`:** one Traefik pod on every node.
- **`service.spec.externalTrafficPolicy: Local`:** a node delivers traffic only to its own Traefik pod, without source NAT. k3s ServiceLB then lists only nodes with a ready Traefik pod as load balancer addresses.

Traefik trusts no upstream proxy, so it replaces any `X-Forwarded-For` a client sends with the address it actually sees. The app's uvicorn trusts the header because only Traefik can reach the app pods (network policies).

## Alternatives considered
- **Keep one Traefik pod and pin it to lark-k3s-01:** the same effect for today's hosts file, but traffic to lark-k3s-02 would be dropped and the ingress would depend on one node.
- **PROXY protocol or another load balancer (MetalLB):** more moving parts for no gain on a flat home network.
- **Have the app read the address some other way:** the address is already gone before the request reaches Traefik.

## Consequences
- Apps receive the real client address in `X-Forwarded-For` and `X-Real-Ip`.
- Each node runs a Traefik pod, so lark-k3s-01 carries one more pod's memory.
- A node whose Traefik pod is not ready drops traffic sent to it, instead of forwarding it to the other node. Clients that use lark-k3s-01's address depend on lark-k3s-01's Traefik pod.
- k3s must not set `node-external-ip` on any node, which k3s documents as incompatible with `externalTrafficPolicy: Local` for traffic that arrives through NAT.
- Rolling back is reverting this change: Argo CD deletes the HelmChartConfig, and k3s is expected to reinstall Traefik with its default values.
