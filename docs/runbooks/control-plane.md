# Runbook: Control plane (k3s servers and etcd)

The Kubernetes API and the cluster's state live on the k3s servers, in embedded etcd. Phase 2 moves from one server with SQLite to three servers with etcd, so the cluster keeps working when any one node is lost.

| Node | Address | Role |
|---|---|---|
| lark-k3s-01 (Pi 5) | 192.168.1.199 | Server; started the etcd cluster (`cluster-init`) |
| lark-k3s-02 (Pi 5) | 192.168.1.200 | Server (promoted from agent) |
| lark-k3s-03 (Pi 4) | 192.168.1.201 | Server |
| Shared API address | 192.168.1.50 | Planned (kube-vip); already in every server's API certificate |

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

## etcd snapshots
k3s takes an etcd snapshot every 12 hours and keeps 5, on each server in `/var/lib/rancher/k3s/server/db/snapshots`. Take one before risky work: `sudo k3s etcd-snapshot save --name before-<change>`. Everything that matters is also in Git (Argo CD rebuilds it) or in Azure (databases), so snapshots save time rather than data.
