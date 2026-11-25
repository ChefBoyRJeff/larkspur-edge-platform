# ADR-0008: Off-site database backups to Azure Blob Storage

- **Status:** Accepted
- **Date:** 11-25-2026

## Context
Each environment's PostgreSQL runs as a single CloudNativePG instance on a local-path volume, so its data lives on one Raspberry Pi's SD card. A failed card, a lost node or a bad change could lose every shipment and the audit trail. The plan requires off-site backups and a tested restore with measured recovery targets, at a cost approved before anything bills.

## Decision
- **Targets (both environments):**
  - **RPO 5 minutes:** at most 5 minutes of changes lost. PostgreSQL ships each WAL file as soon as it fills, and at least every 5 minutes (`archive_timeout`, CloudNativePG's default).
  - **RTO 30 minutes:** a restored database serving within 30 minutes.
- **CloudNativePG's Barman Cloud plugin** (`platform/apps/plugin-barman-cloud.yaml`, plugin v0.16.0) runs as a sidecar in each database pod. It archives every WAL file continuously and takes a **base backup every night at 08:00 UTC** (`ScheduledBackup`), compressed with gzip, keeping **30 days** (`retentionPolicy`). The operator's built-in Barman integration is deprecated in CloudNativePG 1.30, so the plugin is the supported path.
- **Azure Blob Storage** (`azure/backup/`, Bicep): one StorageV2 account in East US, Standard LRS, Hot tier, TLS 1.2 and HTTPS only, no public blob access, 7-day soft delete for blobs and containers, with **one private container per environment**. Hot rather than Cool because the workload is many small writes, which cost more on Cool. A **$5/month budget** filtered to the resource group emails at 50% and 100% actual and 100% forecast.
- **Credentials:** each environment gets a **SAS token for its own container only**, issued from a stored access policy (`barman-<timestamp>`, read/add/create/write/delete/list, 1 year) and sealed into the cluster (`azure/backup/new-backup-credential.ps1`). The script tests the token against its container and checks that the other environment's container refuses it, and after sealing it deletes the container's older policies, which revokes every earlier token. The script never prints the token or passes it on a command line; Git holds only the sealed (encrypted) secret.
- **Network:** a NetworkPolicy lets the database pods reach public addresses on port 443 and nothing private; Azure's addresses change, so a narrower rule is not practical without a proxy.
- **Alerts:** `LarkspurWalArchivingFailing` (critical) and `LarkspurBackupTooOld` (warning, over 26 hours), per environment.
- **Restores are proven by a drill**, `platform/drills/restore-drill.ps1`: it writes a marker row, waits for its WAL to reach Azure, rebuilds a separate cluster from the backups alone, and compares row counts and the marker.

## Alternatives considered
- **Volume snapshots:** local-path has no snapshot support, and a snapshot on the same SD card is not off-site.
- **Account key or connection string:** works everywhere, but gives full control of the whole account and cannot be limited to one container or revoked without rotating the key for everything.
- **Managed identity or workload identity:** the best option on AKS; the Pis have no Azure identity, and Azure Arc for them is Phase 2.
- **An S3-compatible store on site (MinIO on another machine):** no cloud cost, but not off-site, so a fire, theft or power event takes the backups with the data.
- **`pg_dump` to a file share on a schedule:** simple, but only as fresh as the last dump (hours of loss) and no point-in-time recovery.

## Consequences
- **Measured (11-25-2026, dev):**
  - A WAL file reached Azure **2 to 5 seconds** after it was closed.
  - Base backups of the 8 MiB databases took about **20 seconds**.
  - Two restore drills rebuilt dev from Azure in **71 and 81 seconds**, with matching row counts and the marker written just before each drill recovered.
- **Cost:** live East US prices were $0.0208 per GB-month stored and $0.05 per 10,000 writes; for these databases that is estimated at about $0.15 a month at most.
- **Prod restarts once** when the plugin is added (single instance), and a newly created schedule cannot take its first backup while that restart is underway, so the first backup is taken by hand.
- **Credential rotation** must happen before 11-25-2027. Archiving pauses between running the script (which revokes the old token) and the sync of the new sealed secret; WAL waits safely on the node in the meantime.
- **Not yet drilled:** restoring the original cluster in place after losing its node. The procedure is in `docs/runbooks/backups.md` and is exercised in the Operations drills (database pod loss).
- Backups depend on the internet uplink: during an outage WAL accumulates on the node and is shipped when the link returns. A very long outage could fill the SD card first (`LarkspurDatabaseLarge` and the node disk alerts warn).
