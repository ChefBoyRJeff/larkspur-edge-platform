# Runbook: Database backups and restores

How the Larkspur Track databases are backed up to Azure, how to check them, and how to restore. The design is in ADR-0008. Commands use dev; for prod, replace `larkspur-dev` with `larkspur-prod` (and `dev` with `prod`).

## How it works
| What | Where | When | Kept |
|---|---|---|---|
| WAL (every change) | Azure container `larkspur-backups-<env>`, under `<serverName>/wals/` (dev: `larkspur-db-r1`, prod: `larkspur-db`) | As each WAL file fills, and at least every 5 minutes | 30 days |
| Base backup (full copy) | Same container, under `<serverName>/base/` | Nightly at 08:00 UTC (03:00 EST) | 30 days |

- Storage account: `stlarkbkpzw5iikganlkqo` in resource group `rg-lark-edge-backup` (East US, Hot LRS, 7-day soft delete).
- Credential: a SAS token for that environment's container only, in the SealedSecret `larkspur-db-backup-credentials`. **It expires on 11-25-2027.**
- Targets: RPO 5 minutes, RTO 30 minutes. Measured in dev on 11-25-2026: WAL in Azure 2 to 5 seconds after it closed; restore to a healthy new cluster in 71 and 81 seconds. In-place rebuild after a total loss (11-27-2026): database ready in 60 s, nothing lost.
- Alerts: `LarkspurWalArchivingFailing`, `LarkspurBackupTooOld` (see `docs/runbooks/alerts.md`).

## Check the backups
```powershell
kubectl get backups -n larkspur-dev --sort-by=.metadata.creationTimestamp
kubectl get scheduledbackup larkspur-db-nightly -n larkspur-dev
$pod = kubectl get pods -n larkspur-dev -l cnpg.io/cluster=larkspur-db,cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'
kubectl exec -n larkspur-dev $pod -c postgres -- psql -U postgres -tAc "select archived_count, last_archived_wal, last_archived_time, failed_count, last_failed_time from pg_stat_archiver"
```
In Grafana or Prometheus, `time() - barman_cloud_cloudnative_pg_io_last_available_backup_timestamp` is the age of the newest base backup in seconds.

What is in Azure (the account key stays in memory; the command lines carry no token):
```powershell
$acct = az storage account list -g rg-lark-edge-backup --query '[0].name' -o tsv
$key = az storage account keys list -g rg-lark-edge-backup -n $acct --query '[0].value' -o tsv
az storage blob list --account-name $acct --account-key $key -c larkspur-backups-dev --num-results 5000 --query "[].{name:name, size:properties.contentLength}" -o table
Remove-Variable key
```

## Take a backup now
Before risky work (a migration, an operator upgrade), or after a failed nightly backup:
```powershell
$name = "larkspur-db-manual-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"
"apiVersion: postgresql.cnpg.io/v1`nkind: Backup`nmetadata:`n  name: $name`n  namespace: larkspur-dev`nspec:`n  cluster:`n    name: larkspur-db`n  method: plugin`n  pluginConfiguration:`n    name: barman-cloud.cloudnative-pg.io`n" | kubectl apply -f -
kubectl get backup $name -n larkspur-dev -w
```
It should reach `completed` within a minute. Right after the plugin is first added to a cluster, wait until the database pod is ready again (it restarts once), or the backup fails with "requested plugin is not available".

## Restore drill (prove the backups work)
Run every few months, and after any change to backups. It does not touch the running database or the app, apart from a one-row marker table it creates and drops.
```powershell
./platform/drills/restore-drill.ps1 -Environment dev
```
It counts rows, writes a marker and waits for its WAL to reach Azure, rebuilds a separate cluster `larkspur-db-restore` from the backups alone, compares counts and the marker, cleans up, and prints `RESULT: PASS` with the restore time. Add `-KeepRestored` to leave the restored cluster for inspection (delete it and the two `drill-allow-*` network policies afterwards).

## Restore after losing the database
Use when the database cannot come back (its node or SD card is gone) and the data must come from Azure. The rebuilt cluster keeps the name `larkspur-db`, so the app needs no configuration change. **Drilled on dev on 11-27-2026:** the database was ready 60 s after Argo CD re-created it; all rows and a marker written just before the loss were there; the app needed a restart (step 4).
1. **Take stock:** `kubectl get cluster,pods,pvc -n larkspur-prod`. If the node is only rebooting, wait for it instead; the data on the card is newer than the backup.
2. **Change the Cluster in Git first** (`platform/config/databases/larkspur-db-prod.yaml`), in a pull request, and merge it. Bootstrap applies only when a cluster is created, so the change does not touch a running one:
   - replace `bootstrap.initdb` with a recovery from the archive the lost cluster wrote to (its current `serverName`; `larkspur-db` if none is set), keeping the app's database and owner:
     ```yaml
     bootstrap:
       recovery:
         source: origin
         database: larkspur
         owner: larkspur
     externalClusters:
       - name: origin
         plugin:
           name: barman-cloud.cloudnative-pg.io
           parameters:
             barmanObjectName: larkspur-db-backup
             serverName: larkspur-db
     ```
   - give the rebuilt cluster's own archive the next free path, because Barman will not archive into a path that already holds another cluster's WAL: set `serverName: larkspur-db-r<n>` (r1, r2, ...) in the `parameters` of the plugin in `spec.plugins`.
3. **Delete the lost cluster:** `kubectl delete cluster larkspur-db -n larkspur-prod`. Its volume goes with it. This can take about 3 minutes if the pod is still running, because PostgreSQL first waits for open connections to close. Argo CD then re-creates the cluster from Git within seconds, and CloudNativePG restores it from Azure. Watch: `kubectl get cluster larkspur-db -n larkspur-prod -w` until `Cluster in healthy state`.
4. **Restart the app:** the rebuilt cluster gets a new `larkspur-db-app` secret (new password), and the web and sync pods read it only when they start:
   ```powershell
   kubectl rollout restart deployment/larkspur-track deployment/larkspur-track-sync -n larkspur-prod
   ```
5. **Verify:** `https://track.larkspur.home.arpa/readyz` returns 200; the newest shipments and audit entries are there; archiving works (`pg_stat_archiver`, above, shows the newest WAL); and take a base backup now ("Take a backup now"), because the new path has none until the nightly one. Expect `LarkspurBackupTooOld` until then. A few failed archive attempts while the rebuilt database starts are normal.
6. **Afterwards:** leave `bootstrap.recovery` in Git, and update the serverName in "How it works" above. Measured RTO on dev: about 60 s from the cluster being re-created to the database being ready, plus 14 s for the app restart (the delete itself took 3 minutes because the pod was still running).

## Rotate the credential
Before it expires (11-25-2027), or at once if a token may have been exposed:
```powershell
./azure/backup/new-backup-credential.ps1 -Environment dev
```
It issues a new token from a new access policy, tests it (its own container works, the other environment's refuses it), seals it into `platform/config/databases/larkspur-db-dev-backup-credentials.sealedsecret.yaml`, and then deletes the older policies, which revokes every earlier token. **Commit the sealed file and merge right away:** until Argo CD syncs it, the database still holds the revoked token and archiving pauses (WAL waits safely on the node; `LarkspurWalArchivingFailing` fires after 10 minutes). After the sync, check that `last_archived_time` moves again (above).

## Azure resources and cost
- Redeploy or change: `./azure/backup/deploy.ps1` (lint, validate against Azure Policy, what-if, confirmation, deploy). It needs `azure/backup/main.local.bicepparam` (gitignored) with the budget alert email.
- Expected cost: about $0.15 a month at most (live East US prices on 11-25-2026: $0.0208 per GB-month, $0.05 per 10,000 writes). The budget `budget-larkspur-edge-backup` emails at $2.50 and $5.
- **Teardown** (ends backups; do it only when retiring the site): delete the `ScheduledBackup`, `ObjectStore` and `plugins` entries from Git first, then `az group delete -n rg-lark-edge-backup` and `az consumption budget delete --budget-name budget-larkspur-edge-backup`. Soft-deleted data is purged with the account.
