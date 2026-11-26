# ADR-0009: Offline-first sync to Azure Storage Queue through a transactional outbox

- **Status:** Accepted
- **Date:** 11-26-2026

## Context
The warehouse must keep working when its internet uplink is down, and the company's Azure environment must still receive every shipment change once the link returns. A change that only reaches Azure "if the network happens to be up" would lose data during exactly the outages the site is built to survive. Changes for one shipment must be applied in order (received before stored), and a receiver must be able to tell whether it has already seen an event. The plan requires the Azure cost to be approved before anything bills.

## Decision
- **Transactional outbox in the app's own database.** Every shipment change (`create_shipment`, `advance_shipment`) writes an `outbox` row in the **same transaction** as the change (`larkspur_track.outbox.enqueue`), so a change and its event are saved together or not at all. The row carries the event as JSON: `event_id` (the timeline event's UUID), `event_type` (`shipment.received` or `shipment.status_changed`), `occurred_at`, the shipment, the statuses, the note and the actor. The migration back-filled events for the shipments that already existed.
- **A separate sync worker** (`larkspur-track-sync`, one replica per environment, `Recreate` rollout) sends unsent rows **oldest first, one per transaction** (`FOR UPDATE SKIP LOCKED`), marks each sent only after Azure answers `201`, and **stops at the first failure** so later events never overtake an earlier one. After a failure it waits 5 s, doubling up to 300 s; after a success it returns to 5 s. It sends at most 600 events a minute.
- **At-least-once delivery, deduplicated by `event_id`.** If the worker stops after Azure accepted a message but before it marked the row, that event is sent again. Receivers must treat `event_id` as the key. This is "effectively once" for any receiver that does, without distributed transactions.
- **Azure Storage Queue** (`azure/integration/`, Bicep): one StorageV2 account in East US, Standard LRS, TLS 1.2 and HTTPS only, no public blob access, with **one queue per environment** (`larkspur-events-dev`, `larkspur-events-prod`). Each message is `{"schema": 1, "site": "larkspur-<env>", "event": {...}}`, base64-encoded, sent with `messagettl=-1` so it **never expires** while no receiver reads it. A **$5/month budget** filtered to the resource group emails at 50% and 100% actual and 100% forecast.
- **Send-only credential:** each environment gets a SAS token for **its own queue with the add permission only**, issued from a stored access policy (`sync-<timestamp>`, 1 year) and sealed into the cluster (`azure/integration/new-sync-credential.ps1`). The script tests that the token can add to its own queue, cannot read it, and cannot add to the other environment's queue, then deletes the queue's older policies, which revokes every earlier token. A stolen edge credential can add noise to one queue but cannot read or delete anything. The worker sends with Python's standard library only, through the REST API, and never logs the token.
- **Network:** a NetworkPolicy lets only the sync worker reach public addresses on port 443 and nothing private; only Prometheus may reach its metrics port.
- **Alerts:** `LarkspurSyncWorkerDown`, `LarkspurSyncBacklog` (oldest unsent event over 30 minutes) and `LarkspurSyncStalled` (no round for 15 minutes), from the worker's metrics (`larkspur_sync_*`, `larkspur_outbox_*`).
- **Outages are proven by a drill**, `platform/drills/sync-drill.ps1`: it cuts the worker off from Azure, keeps making shipment changes, restarts the worker mid-outage, restores the link through Argo CD and checks that every event arrived.

## Alternatives considered
- **Calling Azure directly from the web request:** simplest, but every change fails (or is silently lost) while the link is down, which defeats the purpose of the site.
- **Azure Service Bus:** ordered sessions, duplicate detection and dead-lettering, but the Basic tier has no sessions or duplicate detection, Standard has a monthly base charge, and the edge would need a SAS on a namespace. The outbox already provides order and an `event_id`; Storage Queue costs cents.
- **Azure Event Hubs or IoT Hub:** built for high-volume telemetry with a fixed hourly or monthly unit price; far more than a few hundred shipment events a day need.
- **Change data capture (logical replication / Debezium) instead of an outbox:** no app changes, but needs a connector service and replication slots on an SD-card database, and the events would be table rows rather than business events.
- **Sending from the web pods in the background:** no extra Deployment, but two replicas would compete for rows and a slow Azure would hold web resources; one worker keeps order simple.

## Consequences
- **Measured (11-25/26-2026):**
  - Dev's 8 existing events reached Azure within 2 s of the worker starting; prod started with an empty outbox.
  - **Uplink-cut drill (dev, 11-26-2026):** during a **48 s** outage, with the worker **restarted mid-outage**, all **7** events made were kept (7 of 7 unsent); **5 s** after Argo CD restored the egress policy the worker had sent all 7, with **0 missing and 0 duplicates**, in outbox order.
  - Sends to a cut link failed at once (`Connection refused`, kube-router rejects) rather than waiting for the 10 s timeout.
- **Cost:** live East US prices (11-25-2026) for Queues on general-purpose v2 accounts: $0.004 per 10,000 operations and $0.045 per GB-month. A few hundred events a day is well under $0.05 a month.
- **Catch-up after a long outage** can take up to 300 s to start (the longest backoff) and then runs at up to 600 events a minute.
- **Duplicates are possible by design**; the drill measured none, but receivers must still dedupe by `event_id`.
- **Order across shipments is the outbox order when sending**, but Azure Storage Queues do not guarantee delivery order; receivers order by `occurred_at` per shipment. Service Bus sessions are the upgrade path if strict order is ever needed.
- **No receiver yet.** The queues hold messages until the company's side (an Azure Function or Logic App, Phase 2) reads them.
- **Credential rotation** must happen before 11-26-2027. Rotating revokes the old token at once; sending pauses (events wait in the outbox) until the new sealed secret is synced and the worker restarted.
- The outbox keeps sent rows; pruning old sent rows is a later improvement (it grows by a few hundred rows a day).
