# ADR-0011: Site DNS with Pi-hole, and name routing on the admin PC

- **Status:** Accepted
- **Date:** 11-27-2026

## Context
The sites (`track`, `track-dev`, `grafana` and `test` under `larkspur.home.arpa`) were found through the admin PC's hosts file. That works for one PC, has to be repeated on every device, and must be edited whenever an address changes. The AT&T gateway is the network's DHCP and DNS server and has no records for the site's names: it answers "name does not exist". Phase 2 reserves a Raspberry Pi 4, lark-dns-01 (192.168.1.202), for site services, and Module 2 adds an Active Directory domain with its own DNS.

## Decision
- **Pi-hole v6 on lark-dns-01**, installed and configured by the Ansible role `pihole` (`site.yml`). The first run uses the official installer, unattended. Every run sets the configuration with `pihole-FTL --config`, which applies it without a restart, and fails unless `track.larkspur.home.arpa` resolves to 192.168.1.50. Settings live in `ansible/roles/pihole/defaults/main.yml`, not in the web interface.
- **Local records:** the sites and the API at the shared address 192.168.1.50 (ADR-0010); each Pi and the switch at its fixed address. Everything else goes to Quad9.
- **The admin PC sends only `*.larkspur.home.arpa` to Pi-hole**, with a Windows Name Resolution Policy Table (NRPT) rule. Its normal DNS stays on the gateway, and the hosts-file entries are removed.
- **The cluster does not use Pi-hole.** Pods resolve through CoreDNS; the blackbox exporter keeps its own `hostAliases`, so page checks do not depend on lark-dns-01.

## Alternatives considered
- **Keep the hosts file:** no new service, but per-device and manual.
- **Point the PC's adapter DNS at Pi-hole first, the gateway second:** tried on 11-27. Windows still returned "name does not exist" for the site names, which only the gateway answers: Pi-hole answered correctly when asked directly, and the PC's other adapters also list the gateway. The NRPT rule routes the site's names deterministically and leaves internet DNS untouched if Pi-hole is down.
- **DNS on the domain controller (Module 2):** not built yet; Pi-hole will forward the domain to it.
- **Expose CoreDNS outside the cluster:** ties the site's DNS to the cluster being healthy.
- **A recursive resolver (Unbound) instead of Quad9:** more privacy, one more service to run; possible later.

## Consequences
- **Measured (11-27-2026):** from the PC, through the NRPT rule and with no hosts entries, `track`, `track-dev` and `grafana` resolve to 192.168.1.50 and all three pages return HTTP 200; `kubectl` (by address) is unaffected. A second Ansible run reports `changed=0` (after #93).
- **One DNS server.** If lark-dns-01 is down, only the site's names stop resolving on the PC; the sites themselves keep running.
- **Every other device needs the same rule, or its DNS pointed at Pi-hole.** The gateway's DHCP still hands out the gateway as the DNS server.
- **Not monitored yet** by Prometheus, and not patched by `patch.yml` (it covers the k3s nodes only).
- **The web password** is set by hand (`sudo pihole setpassword`) and kept in the password manager; the playbook never prints the installer's generated one.
