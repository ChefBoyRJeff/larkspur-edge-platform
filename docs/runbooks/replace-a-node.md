# Runbook: Lose or replace a k3s node (e.g. failed SD card)

## When a node goes down
All three nodes are k3s servers (`docs/runbooks/control-plane.md`), and each database has a copy on two of them, so losing any one node needs no action. **Measured in the node-loss drill (`platform/drills/node-loss-drill.ps1`, lark-k3s-02 holding both database primaries, 11-27-2026, 11:26 PM):**
| Time after the node stops | What happens |
|---|---|
| 0 s | Database connections to the primaries fail; the web pods' readiness check needs the database, so the sites return 503. If kube-vip's address (192.168.1.50) was on the lost node, another server takes it within seconds. |
| about 50 s | The node is `NotReady` (`unreachable` taint); its pods leave the Services. kube-state-metrics, if it was there, moves after 30 s more. |
| about 90 s | The CloudNativePG operator promotes each replica; the `-rw` Services follow and the sites serve again (prod 86 s). `LarkspurDatabaseDown` (2 minutes) does not fire for a failover this fast. |
| about 5 minutes | Kubernetes evicts the node's pods and starts the Deployments elsewhere. On the SD cards this burst of writes makes k3s restart on the other servers: the API drops for about a minute and the sites for 36-48 s (ADR-0010). |
| 10 minutes | `LarkspurDatabaseReplicaMissing` fires: each database runs on one copy. |
| after power-on | The node is Ready in under a minute; its database instances rejoin as replicas. If one stays not ready, see `docs/runbooks/alerts.md`, LarkspurDatabaseReplicaMissing. Pods are not moved back. |

If the lost node held Prometheus, nothing inside the cluster can alert; the healthchecks.io dead man's switch emails about 6 minutes in (`docs/runbooks/alerts.md`, "Dead man's switch").

**If the node is coming back** (power cut, reboot): power it on and check `kubectl get nodes`. Then spread the web pods across the nodes again, which a rolling restart does with no downtime:
```powershell
kubectl rollout restart deployment/larkspur-track -n larkspur-prod
kubectl rollout restart deployment/larkspur-track -n larkspur-dev
```
**If it is not coming back:** replace it (below).

## Replace a node

Use when a node must be rebuilt from a blank card. Expected time: about 20 minutes. The steps below were rehearsed for an agent (worker) in v1.0; all nodes are servers now, and replacing a server has **not** been rehearsed yet.

- **lark-k3s-02 or lark-k3s-03:** follow the steps, using `k3s` instead of `k3s-agent` in step 1. k3s should remove a deleted server from etcd (not yet seen here); check that the remaining two are still members (`kubectl get nodes -L node-role.kubernetes.io/etcd`) before rebuilding. Its database instances' volumes are gone with the card: once the node is back, delete each one's PVC and pod so the operator builds a new replica (`docs/runbooks/alerts.md`, LarkspurDatabaseReplicaMissing).
- **lark-k3s-01: do not rebuild it with the playbook as it is.** The role gives the first server in the inventory `cluster-init`, which would start a second, empty cluster. Move another server to the top of `k3s_server` in `ansible/inventory/hosts.yml` first (pull request), so lark-k3s-01 joins it.

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
- `kubectl get nodes -o wide -L node-role.kubernetes.io/etcd`: node `Ready`, roles `control-plane,etcd`, pinned k3s version.
- A second `ansible-playbook playbooks/site.yml -K` reports `changed=0` on every node.

## Troubleshooting
| Symptom | Cause | Fix |
|---|---|---|
| Agent stuck `activating`; an old `<node>` object shows `NotReady` | Stale node object / node password from the previous install | `kubectl delete node <node>` and delete `<node>.node-password.k3s`; the agent retries and joins |
| `Permission denied (publickey)` from WSL | Ansible key missing from the card | Append `larkspur_ansible.pub` to `~/.ssh/authorized_keys` using the Windows key |
| `REMOTE HOST IDENTIFICATION HAS CHANGED` | New card, new host keys | Step 3 |
| Kernel differs from other nodes | Fresh image is older than patched nodes | Run the patch playbook |
