# Runbook: Sync to Azure

How shipment changes reach Azure, how to check the sync, and how to rotate its credential and run the uplink drill. The design is in ADR-0009; the sync alerts are in `docs/runbooks/alerts.md`. Commands use prod; for dev, replace `larkspur-prod` with `larkspur-dev` (and `prod` with `dev`).

## How it works
| What | Where |
|---|---|
| Every shipment change | Written to the `outbox` table in the same transaction as the change |
| Sync worker | Deployment `larkspur-track-sync` (one replica) sends unsent events oldest first, stops at the first failure, retries with backoff (5 s doubling to 300 s) |
| Azure | Queue `larkspur-events-<env>` in storage account `stlarkintcqcbbxgapai5s`, resource group `rg-lark-edge-integration` (East US, Standard LRS); messages never expire |
| Message | `{"schema": 1, "site": "larkspur-<env>", "event": {"event_id", "event_type", "occurred_at", "shipment", "from_status", "to_status", "note", "actor"}}`, base64-encoded |

- Delivery is **at least once**: an event can arrive twice. Receivers dedupe by `event_id`.
- Credential: a SAS token that can only **add** messages to that environment's queue, in the SealedSecret `larkspur-track-sync-credentials`. **It expires on 11-26-2027.**
- Measured in the uplink drill (dev, 11-26-2026): 7 of 7 events made during a 48 s outage with a worker restart reached Azure 5 s after the link returned; no duplicates.

## Check the sync
```powershell
kubectl get deployment larkspur-track-sync -n larkspur-prod
kubectl logs -n larkspur-prod deploy/larkspur-track-sync --tail=20
$pod = kubectl get pods -n larkspur-prod -l cnpg.io/cluster=larkspur-db,cnpg.io/instanceRole=primary -o jsonpath='{.items[0].metadata.name}'
kubectl exec -n larkspur-prod $pod -c postgres -- psql -U postgres -d larkspur -q -tAc "select count(*) || ' events, ' || count(*) filter (where sent_at is null) || ' unsent, ' || count(last_error) || ' with an error' from outbox"
```
The log shows `sending paused: <reason>` when sending stops and `sending resumed` when it starts again; it never prints the token. `HTTP 403` means the credential expired or was revoked; `URLError` or timeouts mean no internet or Azure is down.

In Prometheus or Grafana: `larkspur_outbox_pending`, `larkspur_outbox_oldest_pending_seconds`, `larkspur_sync_sent_total`, `larkspur_sync_send_failures_total`.

What is in Azure (the account key stays in memory; the command lines carry no token). Peeking does not remove messages:
```powershell
$acct = az storage account list -g rg-lark-edge-integration --query '[0].name' -o tsv
$key = az storage account keys list -g rg-lark-edge-integration -n $acct --query '[0].value' -o tsv
az storage message peek --queue-name larkspur-events-prod --account-name $acct --account-key $key --num-messages 5 -o json --only-show-errors
Remove-Variable key
```
The `content` of each message is base64; decode it with `[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($content))`.

## During an internet outage
Nothing to do. Staff keep working; events wait in the outbox in order. `LarkspurSyncBacklog` fires after 30 minutes and is expected. When the link returns, the worker catches up by itself: within its current backoff (at most 300 s), then up to 600 events a minute. To make it retry at once, restart it (unsent events are kept):
```powershell
kubectl rollout restart deployment/larkspur-track-sync -n larkspur-prod
```

## Rotate the credential
Before it expires (11-26-2027), or at once if a token may have been exposed:
```powershell
./azure/integration/new-sync-credential.ps1 -Environment prod
```
It issues a new token from a new access policy, tests it (it can add to its own queue, cannot read it, and cannot add to the other environment's queue), seals it into `platform/config/sync/larkspur-track-sync-prod-credentials.sealedsecret.yaml`, and then deletes the older policies, which revokes every earlier token.
1. **Commit the sealed file and merge right away.** Until then the worker holds the revoked token, sending pauses with `HTTP 403`, and events wait in the outbox.
2. After Argo CD syncs it, **restart the worker**, because it reads the token only when it starts:
   ```powershell
   kubectl rollout restart deployment/larkspur-track-sync -n larkspur-prod
   ```
3. Check the log shows `sending resumed` (or no `paused` line) and the outbox has 0 unsent (above).

## Uplink-cut drill (prove nothing is lost)
Run in **dev only**, every few months and after any change to the sync. It drains (deletes) every message in the dev queue, and leaves its `DRILL-<time>-<n>` shipments in the dev database.
```powershell
./platform/drills/sync-drill.ps1 -Environment dev
```
It pauses Argo CD self-heal for `root` and `larkspur-track-dev`, deletes the worker's egress policy `allow-larkspur-track-sync-to-azure`, creates 3 shipments and stores them (6 events), restarts the worker, creates one more (1 event), then turns self-heal back on so Argo CD re-creates the policy from Git. It times the catch-up, drains the queue, and prints `RESULT: PASS` when all 7 events arrived. Self-heal is always turned back on, even if the drill fails.

If a drill was interrupted (the terminal closed), check and restore:
```powershell
kubectl get application root larkspur-track-dev -n argocd -o jsonpath='{range .items[*]}{.metadata.name}: {.spec.syncPolicy.automated}{"\n"}{end}'
kubectl apply -f platform/bootstrap/root-app.yaml
```
Re-applying the root app turns its self-heal back on, and root then restores `larkspur-track-dev` and the policy from Git.

## Azure resources and cost
- Redeploy or change: `./azure/integration/deploy.ps1` (lint, validate against Azure Policy, what-if, confirmation, deploy). It needs `azure/integration/main.local.bicepparam` (gitignored) with the budget alert email.
- Expected cost: well under $0.05 a month (live East US prices on 11-25-2026: $0.004 per 10,000 operations, $0.045 per GB-month). The budget `budget-larkspur-edge-integration` emails at $2.50 and $5.
- **Teardown** (ends sync; do it only when retiring the site): set `sync.enabled: false` in both values files and delete the sealed credentials from Git first, then `az group delete -n rg-lark-edge-integration` and `az consumption budget delete --budget-name budget-larkspur-edge-integration`. Unsent events stay in the outbox.
