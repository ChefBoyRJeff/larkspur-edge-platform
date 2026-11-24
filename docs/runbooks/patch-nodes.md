# Runbook: Patch cluster nodes

Applies OS updates (including the kernel) to every node with no cluster downtime. k3s is not upgraded by this runbook; k3s upgrades change `k3s_version` in `ansible/inventory/group_vars/k3s_cluster.yml` through a pull request.

## When
- Monthly, or when a security update or kernel version drift between nodes is found.
- Check drift with `kubectl get nodes -o wide` (KERNEL-VERSION column).

## Procedure (WSL)
1. `cd /mnt/c/Development/larkspur-edge-platform/ansible`
2. Confirm the cluster is healthy: `kubectl get nodes` shows every node Ready (run from PowerShell).
3. Run `ansible-playbook playbooks/patch.yml -K`.
4. The playbook patches one node at a time, worker first: drain, upgrade, reboot if packages changed, wait for Ready, uncordon.

## Verify
- `kubectl get nodes -o wide`: every node Ready, schedulable, and on the same kernel version.
- `ansible-playbook playbooks/site.yml -K` still reports `changed=0` (patching must not change managed configuration).

## If a node does not return
- The playbook stops before touching the next node, so only one node is affected.
- Check power and network, then `ssh larkadmin@<node>.local` and `sudo journalctl -b -p err`.
- If it boots but stays NotReady: `sudo systemctl status k3s` or `k3s-agent`.
- If the card is damaged: follow `replace-a-node.md`.
