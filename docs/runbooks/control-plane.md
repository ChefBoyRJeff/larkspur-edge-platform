# Runbook: Control plane (k3s servers and etcd)

The Kubernetes API and the cluster's state live on the k3s servers, in embedded etcd. Phase 2 moved from one server with SQLite to three servers with etcd, so the cluster keeps working when any one node is lost (ADR-0010).

| Node | Address | Role |
|---|---|---|
| lark-k3s-01 (Pi 5) | 192.168.1.199 | Server; started the etcd cluster (`cluster-init`) |
| lark-k3s-02 (Pi 5) | 192.168.1.200 | Server (promoted from agent) |
| lark-k3s-03 (Pi 4) | 192.168.1.201 | Server |
| Shared API address | 192.168.1.50 | kube-vip (`platform/apps/kube-vip.yaml`): held by one server at a time, moves within seconds; in every server's API certificate. The admin PC's kubeconfig uses it. |

Ansible owns all of it: `ansible/inventory/hosts.yml` (which nodes are servers), `roles/k3s_server` (each server's `/etc/rancher/k3s/config.yaml`). Run from WSL:
```bash
cd /mnt/c/Development/larkspur-edge-platform/ansible
ansible-playbook playbooks/site.yml -K
```
Servers are configured one at a time, the first one first.

## Quorum
etcd needs a majority of its members: with 3 servers, any 1 can be lost; with 2, losing either one stops the API (running pods keep running, but nothing can be changed or rescheduled). **Two servers is only a step on the way to three**; do not stay there.

## Check
```powershell
kubectl get nodes -L node-role.kubernetes.io/etcd
```
Every server shows `control-plane,etcd` and `true`.

## Converting the first server from SQLite (done once)
The first `site.yml` run after adding `cluster-init` stops k3s on lark-k3s-01, keeps a copy of the SQLite datastore in `/var/lib/rancher/k3s/server/sqlite-before-etcd.tar.gz`, and starts it again with `cluster-init`, which converts the datastore to etcd. Running pods are not restarted.

**Undo (only while lark-k3s-01 is the only server):** on lark-k3s-01, `sudo systemctl stop k3s`, move `/var/lib/rancher/k3s/server/db/etcd` aside, restore the archive into `/var/lib/rancher/k3s/server/` (`sudo tar -xzf sqlite-before-etcd.tar.gz -C /var/lib/rancher/k3s/server`), remove `cluster-init` from `/etc/rancher/k3s/config.yaml`, and start k3s.

## Promote an agent to a server
List the node under `k3s_server` in `ansible/inventory/hosts.yml` (merged), then:
```bash
ansible-playbook playbooks/promote-to-server.yml -K -e node=lark-k3s-02
```
It refuses if a local volume lives on the node, drains it, uninstalls the agent, deletes the old node object, installs k3s as a server that joins etcd, and checks it is an etcd member. Pods do not move back on their own.

## Slow disks (SD cards)
All three servers run etcd on SD cards. Even at rest, etcd logs `apply request took too long` (200-700 ms against an expected 100 ms). In the lark-k3s-01 node-loss drill on 11-27 the two remaining servers logged `slow fdatasync` 30 times and lost the etcd leader 3 times each while 23 evicted pods were rescheduled; k3s exited with `leaderelection lost for k3s-etcd`, and because each server's etcd runs inside k3s, one restart took the other below quorum. The API was down from about 04:16 to 04:19 PM.

Mitigation: `k3s_etcd_args` in `ansible/inventory/group_vars/k3s_cluster.yml` sets `heartbeat-interval=500` and `election-timeout=5000`, applied by `site.yml` (one server at a time). The lasting fix is SSD storage for `/var/lib/rancher/k3s/server/db`.

Check for disk pressure: `journalctl -u k3s --since -1h | grep -c 'slow fdatasync'` on a server.

## etcd snapshots
k3s takes an etcd snapshot every 12 hours and keeps 5, on each server in `/var/lib/rancher/k3s/server/db/snapshots`. Take one before risky work: `sudo k3s etcd-snapshot save --name before-<change>`. Everything that matters is also in Git (Argo CD rebuilds it) or in Azure (databases), so snapshots save time rather than data.
