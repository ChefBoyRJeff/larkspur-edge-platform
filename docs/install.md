# Install Larkspur Edge from scratch

How to rebuild the whole system on blank SD cards: the nodes, k3s, the platform, the secrets, Azure and both environments. Most steps link to the runbook that holds the detail. Expect about two hours, most of it waiting.

Use this guide for a new site or after losing two or more nodes. To replace one node, use `docs/runbooks/replace-a-node.md` instead. This guide has not yet been run end to end on blank cards; its steps come from the procedures used to build the site.

## What you need
| What | Notes |
|---|---|
| 3 Raspberry Pis for the cluster and 1 for DNS, with SD cards, power, and a LAN with fixed addresses | k3s servers `lark-k3s-01` (Pi 5, 192.168.1.199), `lark-k3s-02` (Pi 5, .200), `lark-k3s-03` (Pi 4, .201); Pi-hole on `lark-dns-01` (Pi 4, .202); 192.168.1.50 free for kube-vip, outside the DHCP range |
| Admin PC: Windows, PowerShell 7, Git, VS Code | Clone this repository to `C:\Development\larkspur-edge-platform` |
| WSL (Ubuntu) with Ansible and the `larkspur_ansible` SSH key | For the node playbooks |
| kubectl, kubeseal 0.41.2 (`%LOCALAPPDATA%\Programs\kubeseal`), Azure CLI with Bicep | kubeseal must match the controller in `platform/apps/sealed-secrets.yaml` |
| Helm | Only to install Argo CD once (step 4) |
| Raspberry Pi Imager | Step 1 |
| A password manager (Bitwarden) | Every password in this guide is created and stored there; none is ever pasted into chat, Git or a ticket |
| GitHub access to `ChefBoyRJeff/larkspur-edge-platform`, an Azure subscription, a Gmail account with an app password | |

## 1. Flash the cards
Follow `docs/runbooks/replace-a-node.md`, "2. Flash a new card", once for each Pi, with hostnames `lark-k3s-01`, `lark-k3s-02`, `lark-k3s-03` and `lark-dns-01`. If the PC knew earlier cards, forget their host keys (same runbook, step 3).

## 2. Install k3s with Ansible (WSL)
```bash
cd /mnt/c/Development/larkspur-edge-platform/ansible
ansible-playbook playbooks/site.yml -K
```
It configures every Pi, installs the pinned k3s version (`ansible/inventory/group_vars/k3s_cluster.yml`) on the servers one at a time (lark-k3s-01 starts the etcd cluster, the others join it), and installs Pi-hole on lark-dns-01. The k3s nodes reboot once. Running it a second time must report `changed=0`. Then set the Pi-hole web password (`docs/runbooks/dns.md`).

## 3. Give the PC access to the cluster (PowerShell)
The kubeconfig on `lark-k3s-01` is a cluster-admin credential: copy it only to the admin PC.
```powershell
ssh -t larkadmin@lark-k3s-01.local 'sudo install -m 600 -o larkadmin /etc/rancher/k3s/k3s.yaml /home/larkadmin/k3s.yaml'
scp larkadmin@lark-k3s-01.local:k3s.yaml $HOME\.kube\larkspur.yaml
ssh larkadmin@lark-k3s-01.local 'rm /home/larkadmin/k3s.yaml'
(Get-Content $HOME\.kube\larkspur.yaml) -replace '127.0.0.1', '192.168.1.199' -replace ': default$', ': larkspur-labs' | Set-Content $HOME\.kube\larkspur.yaml
$env:KUBECONFIG = "$HOME\.kube\larkspur.yaml"; kubectl get nodes
```
All three nodes must be `Ready`. Make the file your default kubeconfig (or set `KUBECONFIG` in your PowerShell profile). After step 4, when kube-vip runs, change the server to the shared address so the PC keeps working if lark-k3s-01 is down: `(Get-Content $HOME\.kube\larkspur.yaml) -replace '192.168.1.199', '192.168.1.50' | Set-Content $HOME\.kube\larkspur.yaml`.

## 4. Install Argo CD and hand over to Git
Follow `docs/runbooks/bootstrap-argocd.md`. After `kubectl apply -f platform/bootstrap/root-app.yaml`, Argo CD installs everything in `platform/apps/` from `main`.

**Expected at this point:** the SealedSecrets in Git cannot be decrypted, because the new Sealed Secrets controller has a new key. Alertmanager, Grafana, the database backups and the sync workers wait for their secrets (step 7), and the databases wait for step 8.

## 5. Trust the new root CA and name the sites (PowerShell)
cert-manager creates a new Larkspur Labs Root CA on every new cluster. Trust it for your Windows user only:
```powershell
$b64 = kubectl get secret larkspur-root-ca -n cert-manager -o jsonpath='{.data.ca\.crt}'
[IO.File]::WriteAllBytes("$HOME\larkspur-root-ca.crt", [Convert]::FromBase64String($b64))
Import-Certificate -FilePath "$HOME\larkspur-root-ca.crt" -CertStoreLocation Cert:\CurrentUser\Root
```
Remove the previous cluster's "Larkspur Labs Root CA" in `certmgr.msc` (Trusted Root Certification Authorities), so only the current one is trusted.

Then send the site's names to Pi-hole with the NRPT rule in `docs/runbooks/dns.md`, "Point the admin PC at Pi-hole" (PowerShell as Administrator). No hosts-file entries are needed.

## 6. Azure
If the resource groups `rg-lark-edge-backup` and `rg-lark-edge-integration` still exist, keep them: they hold the backups, and nothing else is needed here. Otherwise **check the cost first** (ADR-0008 and ADR-0009: well under $1 a month together), then deploy each one with its script. Each needs a `main.local.bicepparam` (gitignored) with the budget alert email:
```powershell
./azure/backup/deploy.ps1
./azure/integration/deploy.ps1
```
Each script lints, validates against Azure Policy, shows a what-if and asks before deploying.

## 7. Seal the secrets for the new cluster
Each script asks for its values and seals them with the new cluster's key. Commit the sealed files in one pull request, then merge it.
| Secret | How |
|---|---|
| Alertmanager email (Gmail app password) | `./platform/alertmanager/seal-config.ps1` (`docs/runbooks/alerts.md`, "Change the alert email settings") |
| Grafana admin password | `docs/runbooks/alerts.md`, "Change the Grafana admin password" |
| Database backup credentials | `./azure/backup/new-backup-credential.ps1 -Environment dev`, then `-Environment prod` |
| Sync credentials | `./azure/integration/new-sync-credential.ps1 -Environment dev`, then `-Environment prod` |

After Argo CD syncs, restart the sync workers (`kubectl rollout restart deployment/larkspur-track-sync -n larkspur-dev`, and the same for `larkspur-prod`).

## 8. Databases: restore from Azure or start empty
A new database cannot archive into an Azure path that already holds WAL, so each environment's Cluster in `platform/config/databases/` needs a decision before it starts:
- **Restore (keep the data, normal case):** set `bootstrap.recovery` from the path the old cluster archived to, and give the new one the next free `serverName`, exactly as in `docs/runbooks/backups.md`, "Restore after losing the database", step 2. Check which paths exist with the "What is in Azure" commands in that runbook (list the container and look at the folder names). On 11-27-2026, dev archived to `larkspur-db-r1` and prod to `larkspur-db`.
- **Start empty:** keep `bootstrap.initdb`, and still set a new `serverName` for the archive.

Merge the change. After the databases are healthy, restart the web and sync pods (they read the new database password only at start), then take a base backup in each environment (`docs/runbooks/backups.md`, "Take a backup now").

## 9. People
If the databases started empty, create the first admin of each environment (`docs/runbooks/user-administration.md`, "Create the first admin of an environment"). Restored databases keep their users.

## 10. Check the result
| Check | Expected |
|---|---|
| `kubectl get applications -n argocd` | Every application `Synced` and `Healthy` |
| https://track.larkspur.home.arpa, https://track-dev.larkspur.home.arpa, https://grafana.larkspur.home.arpa | Load without a certificate warning; Larkspur Track shows the release in the footer |
| Alert email | The test alert in `docs/runbooks/alerts.md`, "Change the alert email settings", step 5, arrives as a FIRING email |
| Backups | `./platform/drills/restore-drill.ps1 -Environment dev` prints `RESULT: PASS` |
| Sync | The sync worker logs `sync worker started`; `LarkspurSyncBacklog` is not firing |

## Known limits
- **The Sealed Secrets key is not backed up**, so every new cluster needs step 7. Backing it up (`kubectl get secret -n kube-system -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml`) would let the sealed files in Git work again, but the file is as sensitive as all the secrets together and must be kept offline.
- Each database has a copy on two nodes, and etcd needs two of the three servers. Losing two nodes stops the API, and can lose both copies of a database; then the databases come back only from Azure (step 8).
- All nodes run from SD cards; see ADR-0010 for what that costs during a node loss.
- Each new cluster has a new root CA, so every PC must trust it again (step 5).
