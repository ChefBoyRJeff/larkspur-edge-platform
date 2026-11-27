# Runbook: Lose or replace a k3s node (e.g. failed SD card)

## When a worker node goes down
**Measured in the node-loss drill (`platform/drills/node-loss-drill.ps1`, lark-k3s-02, 11-27-2026):**
| Time after the node stops | What happens |
|---|---|
| 0 to about 50 s | Kubernetes still lists the node's pods. Traefik tries a web pod on it, gives up after 2 s and retries on the other node's pod, so users see a short delay (prod 0, dev 1 failed request out of about 500). Before the retry (first run) 8 requests failed in prod and 7 in dev. |
| about 50 s | The node is `NotReady` (`unreachable` taint); its pods leave the Services. One Alertmanager keeps emailing; `AlertmanagerClusterDown` and `KubeNodeNotReady` fire, as expected. |
| about 5 minutes | Kubernetes reschedules the node's Deployments (web pod, sync worker, CoreDNS, cert-manager) on the other node. `LarkspurSyncWorkerDown` may fire; events wait in the outbox. The StatefulSet pod (one Alertmanager) waits for the node. |
| After power-on | The node is Ready within about 20 s. Pods are not moved back. |

Limits: each database lives on lark-k3s-01's card (local-path volumes), so **losing lark-k3s-01 stops both databases and the Kubernetes API** until it returns, or until the databases are rebuilt from Azure (`docs/runbooks/backups.md`, "Restore after losing the database"). k3s runs one CoreDNS and manages its manifest itself; the app keeps its open database connections while CoreDNS moves.

**If the node is coming back** (power cut, reboot): power it on and check `kubectl get nodes`. Then spread the web pods across both nodes again, which a rolling restart does with no downtime:
```powershell
kubectl rollout restart deployment/larkspur-track -n larkspur-prod
kubectl rollout restart deployment/larkspur-track -n larkspur-dev
```
**If it is not coming back:** replace it (below).

## Replace a node

Use when a worker node must be rebuilt from a blank card. Expected time: about 20 minutes.

## Prerequisites
- WSL Ubuntu with Ansible and the `larkspur_ansible` key (unlocked by keychain).
- kubectl on the admin PC (context `larkspur-labs`).
- Raspberry Pi Imager; the Pi password from the password manager.

## 1. Stop the old node FIRST, then remove it from the cluster
Order matters: a running agent re-registers itself immediately after its node object is deleted, and the rebuilt node is then rejected because its name is already taken with a different node password.

```
kubectl drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=120s
ssh -t larkadmin@<node>.local "sudo systemctl stop k3s-agent && sudo shutdown -h now"
kubectl delete node <node>
kubectl -n kube-system delete secret <node>.node-password.k3s --ignore-not-found
kubectl get nodes
```

The node must no longer be listed.

## 2. Flash a new card
Raspberry Pi Imager: Raspberry Pi OS Lite (64-bit); hostname `<node>`; user `larkadmin` (same password); Wi-Fi off; time zone `America/New_York`; SSH public-key only with **both** public keys (`larkspur-labs` and `larkspur-ansible`). Boot and wait about 3 minutes.

## 3. Forget the old host keys
- PowerShell: `ssh-keygen -R <node>.local`
- WSL: `ssh-keygen -R <node-ip>`

Reconnect once from each side with `-o StrictHostKeyChecking=accept-new`.

## 4. Rebuild with Ansible (WSL)
```
cd /mnt/c/Development/larkspur-edge-platform/ansible
ansible-playbook playbooks/site.yml -K
```

The node reboots once (memory cgroup) and rejoins.

## 5. Verify
- `kubectl get nodes -o wide`: node `Ready`, role `worker`, pinned k3s version.
- A second `ansible-playbook playbooks/site.yml -K` reports `changed=0` on every node.

## Troubleshooting
| Symptom | Cause | Fix |
|---|---|---|
| Agent stuck `activating`; an old `<node>` object shows `NotReady` | Stale node object / node password from the previous install | `kubectl delete node <node>` and delete `<node>.node-password.k3s`; the agent retries and joins |
| `Permission denied (publickey)` from WSL | Ansible key missing from the card | Append `larkspur_ansible.pub` to `~/.ssh/authorized_keys` using the Windows key |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | New card, new host keys | Step 3 |
| Kernel differs from other nodes | Fresh image is older than patched nodes | Run the patch playbook |
