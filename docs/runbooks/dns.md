# Site DNS (Pi-hole on lark-dns-01)

Pi-hole on `lark-dns-01` (192.168.1.202, Raspberry Pi 4) answers DNS for the site: local names under `larkspur.home.arpa` and everything else through Quad9. It replaces the hosts-file entries on the admin PC (ADR-0011). The cluster does not use it: pods resolve through CoreDNS, and the blackbox exporter keeps its own `hostAliases`.

| Name | Address |
|---|---|
| track, track-dev, grafana, test, k3s-api `.larkspur.home.arpa` | 192.168.1.50 (kube-vip shared address) |
| lark-sw01, lark-k3s-01..03, lark-dns-01 `.larkspur.home.arpa` | the device's fixed address |

The records and upstreams live in `ansible/roles/pihole/defaults/main.yml`. To add or change one, edit that list and rerun the playbook; Ansible sets them with `pihole-FTL --config`, which applies them without a restart. Do not change them in the web interface: the next run puts the list back.

## Install or update
WSL, from `ansible/`:
```bash
ansible-playbook playbooks/site.yml -K --limit lark-dns-01
```
The first run downloads the official installer and runs it unattended (a few minutes). Every run ends by asking Pi-hole for `track.larkspur.home.arpa` and fails unless it answers 192.168.1.50.

## Set the web password
The installer sets a random password and the playbook does not print it. Create one in the password manager, then:
```powershell
ssh -t larkadmin@lark-dns-01.local "sudo pihole setpassword"
```
The web interface is `http://192.168.1.202/admin/`.

## Check
PowerShell, from any device on the network:
```powershell
Resolve-DnsName track.larkspur.home.arpa -Server 192.168.1.202 -DnsOnly
Resolve-DnsName microsoft.com -Server 192.168.1.202 -DnsOnly
```

## Point the admin PC at Pi-hole
Send only the site's names to Pi-hole with a Name Resolution Policy Table (NRPT) rule; the PC's normal DNS stays on the gateway (ADR-0011). Pointing the adapter's DNS at Pi-hole first was tried and did not work: Windows still got "name does not exist" from the gateway. PowerShell as Administrator:
```powershell
Add-DnsClientNrptRule -Namespace '.larkspur.home.arpa' -NameServers 192.168.1.202 -Comment 'Larkspur site DNS (Pi-hole on lark-dns-01)'
Clear-DnsClientCache
Resolve-DnsName track.larkspur.home.arpa -DnsOnly
```
When it answers 192.168.1.50, back up `C:\Windows\System32\drivers\etc\hosts` and remove its `larkspur.home.arpa` lines. Check: `Get-DnsClientNrptRule`, then open the sites.

**Undo:** `Get-DnsClientNrptRule | Where-Object Namespace -contains '.larkspur.home.arpa' | Remove-DnsClientNrptRule -Force`, and restore the hosts file from its backup.

## Limits
- One DNS server. If lark-dns-01 is down, the site's names stop resolving on the PC (internet names still work through the gateway); the sites themselves keep running.
- Not monitored by Prometheus yet, and not patched by `patch.yml` (patch it with `sudo apt update && sudo apt full-upgrade` and `sudo pihole -up`).
- The Active Directory domain (Phase 2, Module 2) will need a conditional forwarder from Pi-hole to the domain controller.
