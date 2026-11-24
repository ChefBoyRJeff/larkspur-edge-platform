# RCA-001: Ansible group variables not loaded (node_timezone undefined)

- **Date:** 11-24-2026
- **Milestone:** M1 (Ansible)
- **Impact:** None. Caught in check mode (dry run) before any change reached the nodes.

## Symptoms
The first dry run failed on both nodes at the "Time zone set" task:
`Error while resolving value for 'name': 'node_timezone' is undefined`,
although `ansible/group_vars/k3s_cluster.yml` defined it. Lint and `--syntax-check` had both passed.

## Hypothesis
The variable file was never loaded, so its location, not its contents, was wrong.

## Root cause
Ansible loads `group_vars/` only from the directory containing the **inventory file** or the **playbook**.
The file was in `ansible/group_vars/`, but the inventory is `ansible/inventory/hosts.yml` and the playbook
is `ansible/playbooks/site.yml`, so neither lookup location matched.

## Remediation
Moved the file to `ansible/inventory/group_vars/k3s_cluster.yml` (next to the inventory).

## Verification
`ansible-inventory --host <node>` listed `node_timezone`, `k3s_version` and the other variables for both
nodes; the dry run then completed with `failed=0` (see `docs/evidence/m1-01-apply.txt` onward).

## Prevention
- Run `ansible-inventory --host <node>` to confirm resolved variables before a new playbook's first run.
- Always dry-run (`--check --diff`) before applying.
- Lint and syntax checks do not catch unresolved variables: they validate structure, not values.
