# Interview guide

How to present Larkspur Edge in a technical interview, and a study reference for anyone reading the repository. It covers what the project is, how to walk through it, the problems worth telling, and full answers to the questions an interviewer is likely to ask, with the concept behind each answer explained.

Every number here comes from a drill or check that was actually run; the pull request numbers point to the change and its "Verified" line. Larkspur Edge is a personal project run on my own hardware to production practices: everything in Git, every change by pull request, every recovery path tested.

## The 30-second version
Larkspur Track is a shipment-tracking app for a fictional warehouse that must keep working when its internet link is down. It runs on site on a three-server Kubernetes cluster (k3s) on Raspberry Pis, with a virtual IP in front of the API and two copies of each database. Everything is in Git and deployed by Argo CD. The databases back up continuously to Azure Blob Storage, and shipment events sync to an Azure queue when the link is up. I proved the recovery paths with drills: a bad release, losing the database, losing a node, and cutting the internet link. Each drill found something I then fixed; losing the node that held both databases went from an 11-minute outage to about 90 seconds.

## 3–5 minute walkthrough
1. **The problem (30 s).** A warehouse needs tracking that does not stop when the WAN goes down, and the company needs the data in Azure. So the app runs on site, and Azure is for backups and sync, not for serving users.
2. **The platform (1 min).** Three Pis run k3s as servers with embedded etcd (`lark-k3s-01`, `-02`, `-03`), so the cluster survives losing any one of them. kube-vip gives the Kubernetes API one address (192.168.1.50) that moves to a healthy server. Ansible builds the nodes. Argo CD syncs everything from `main` (app of apps); changes happen only by pull request. cert-manager runs an internal CA for HTTPS, Sealed Secrets keeps secrets encrypted in Git, and every app namespace is default-deny with restricted pod security.
3. **The app and delivery (1 min).** FastAPI with server-rendered pages and PostgreSQL (CloudNativePG, a primary and a streaming replica on different nodes), with sign-in, three roles and an append-only audit log. GitHub Actions tests every pull request, builds amd64 and arm64 images, and scans them with Trivy. Releasing is a pull request that changes an immutable image tag; rolling back is reverting it.
4. **Operations (1 min).** Prometheus, Grafana and Alertmanager, with alerts emailed and each one linked to a runbook section. 7-day SLOs for prod (99.5% of requests succeed, 99% under 1 s) with burn-rate alerts. A blackbox exporter checks the pages the way a browser does. An outside dead man's switch (healthchecks.io) emails if the alerting itself stops.
5. **Resilience (1 min).** WAL archiving and nightly backups to Azure (target: lose at most 5 minutes, back within 30). A transactional outbox and sync worker send events to Azure Storage Queue at least once. Then the drills and what they changed (below).

## 10-minute deep dive (pick by audience)
- **High availability:** three etcd members tolerate one loss; kube-vip moves the API address in seconds; the database operator and its backup plugin each run two replicas on different nodes, so a standby can promote the database replica. Measured on 11-27: losing the node with both database primaries took prod down for 11 min 6 s before the fixes and 1 min 33 s after (#84–#87).
- **GitOps and release safety:** sync waves (network policies, then the migration hook, then the Deployment), so a failed migration stops a release before any pod changes. The bad-release drill showed Argo CD will not start a revert's sync while a stuck one is retrying; fixed with `retry.refresh`, a CI check that the image tag exists, and a `LarkspurReleaseStuck` alert (#67–#69).
- **Data protection:** the Barman Cloud plugin archives WAL to a per-environment container with a SAS token for that container only. Measured: WAL reaches Azure in 2–5 s; restore drills rebuilt a database in 71–81 s with every row and a marker present (#56, #57). The full-loss drill rebuilt dev in place from Azure in 60 s (#70) and showed the app needs a restart because the rebuilt database gets a new password.
- **Offline-first sync:** the change and its outbox row commit in one transaction; one worker sends oldest-first and stops at the first failure, so order holds; `event_id` makes duplicates harmless. Measured: 7 of 7 events made during a 48 s outage, with a worker restart in the middle, reached Azure 5 s after the link returned, no duplicates (#65).
- **Security:** least-privilege credentials (backup token per container; sync token can only add messages, tested to fail on read and on the other queue), default-deny network policies, non-root read-only containers, actions pinned to commit SHAs, no plain secrets in Git.
- **Observability:** SLO burn rates (16.8x / 7x / 3.5x) with a minimum request count, so a quiet dev cluster does not page; promtool unit tests for alert rules; a dead man's switch for the case where Prometheus itself is down.

## Problems worth telling (situation, action, result)
| Story | What happened | What I did | Result |
|---|---|---|---|
| Losing the node with both databases | Powering off `lark-k3s-01` took both sites down for 11 min 6 s, and no alert email was sent. The only database operator was on that node; the only Prometheus too. At the 5-minute eviction, etcd on the SD cards could not keep up and k3s crashed on both surviving servers | Read the k3s and operator logs on each node: `slow fdatasync`, `leaderelection lost for k3s-etcd`, operator crash-looping without an API. Added a second operator replica (#84), longer etcd timeouts (#85) and an outside dead man's switch (#86). A second drill then showed the operator stuck on `Error loading plugins`: the backup plugin was the next single point of failure (#87) | Final drill: database failover in under 90 s, prod back in 1 min 33 s (38 failed checks instead of 467), "alerting is down" email 6 minutes in |
| A leaked token | While sealing the backup credential, a PowerShell `Set-Content` call treated the SAS token as a file path and printed it in its error message | Revoked it at once by deleting its access policy, then hardened the script: timestamped policies, revoke-older, tests over REST, no token on any command line, masked errors (#54) | No use of the token was possible after revocation; the pattern was removed from the runbooks too (#55) |
| A dashboard that went blank | The availability SLO showed "no data" whenever there were no errors, because the error series did not exist | Rewrote the ratio as `(errors or 0 * total) / total` and added a regression test (#51) | 7-day availability reads 100% instead of blank |
| A stuck release | A release with a missing image left the migration job pulling forever; users saw nothing (593 of 593 requests fine) and Argo CD said Healthy, so nothing alerted, and a revert did not apply | Predicted the behaviour from Argo CD's source, confirmed it in the drill, stopped the stuck sync, then fixed the three gaps (#67–#69) | Reverts are picked up on the next retry, bad tags fail CI, and a stuck release alerts within 5 minutes |
| Losing a worker node | Powering off the worker caused about 50 s of failed requests (8 in prod, 7 in dev) and stopped alert email for 8 minutes, because the only Alertmanager was stuck on that node | Two Alertmanagers on different nodes; a Traefik retry with a 2 s connect timeout (#73) | Re-run: 0 of 498 prod and 1 of 499 dev requests failed; alert emails arrived during the outage |
| A runbook that was wrong | The never-tested "restore after losing the database" procedure missed the app restart and had the steps in a risky order | Ran it as a drill on dev and rewrote it from what happened (#70, #71) | Measured rebuild: 60 s to a ready database, nothing lost |

## Likely questions, with answers

### Architecture
**Why k3s on Raspberry Pis instead of a cloud service?**
The requirement is to keep working without the internet, so the system has to run on site. k3s is a certified Kubernetes distribution packaged as one small binary, which fits Pis with 4–8 GB of memory. The same Helm chart is meant to run on AKS (amd64 images are built), but I have not run it there.
*Concept:* edge computing puts the workload where the users and data are, and uses the cloud for what does not need to be on site (backups, reporting, integration).

**How does the cluster survive losing a server?**
The three servers run etcd, the database that stores every Kubernetes object. etcd needs a majority (quorum) of its members to accept a write: with 3 members the quorum is 2, so one can fail. The API address 192.168.1.50 belongs to whichever server kube-vip elects; when that server dies, another one takes the address within seconds (within 11 s in the lark-k3s-02 drill) by announcing it on the network with ARP.
*Concept:* quorum is `floor(n/2) + 1`. Three members tolerate one failure, five tolerate two; an even number adds no tolerance (4 also tolerates only one).

**How does the database fail over?**
CloudNativePG runs a primary and a streaming replica on different nodes (required anti-affinity). The app connects through the `-rw` Service, which always points at the primary. When the primary's node stops, Kubernetes marks its pod not ready (about 40–50 s); the operator then promotes the replica and moves the Service to it. Measured: promotion between 78 and 89 s after power-off, sites serving at 93 s. When the old node returns, its instance rejoins as the new replica.
*Concept:* RTO for a node loss is now set by failure detection (how long Kubernetes waits before calling a node unreachable), not by restoring data.

**What is the biggest weakness now?**
Storage speed. All three servers run etcd on SD cards, which are slow to flush writes to disk. At rest etcd already logs requests taking 200–700 ms against an expected 100 ms. When a node is lost, Kubernetes evicts its pods at the 5-minute mark; that burst of writes made k3s restart on the surviving servers, and because etcd runs inside k3s, one restart takes the other below quorum. Longer etcd timeouts cut that outage from about 3 minutes to about 1 minute; SSDs are the real fix. Other limits: one Prometheus (covered by the dead man's switch, but there is no metrics history during a loss of its node), and one site with one power feed.

### Delivery
**How do you deploy and roll back?**
A pull request changes the image tag in `values-<env>.yaml`; Argo CD applies it after the merge. Prod only gets a tag dev is running. Rolling back is GitHub's Revert button on that pull request. Measured in the CI/CD rollback drill: about 30 s from the merged revert to the old release serving.
*Concept:* GitOps means Git is the source of truth and a controller in the cluster pulls changes, so the cluster never needs credentials pushed into it from CI, and every change has a review and an audit trail.

**What happens if a migration fails?**
It runs as an Argo CD Sync hook before the new pods (sync waves), so the sync stops and the old release keeps serving. Migrations must stay backward compatible (add columns before using them, drop them a release later) so a rollback still works against the new schema.

**How do you stop a bad image from being released?**
CI fails a promotion whose image tag does not exist in the registry, and the `LarkspurReleaseStuck` alert fires if a sync keeps retrying for more than 5 minutes. Images are immutable tags (`sha-<commit>`), so a tag always means the same build.

### Data
**How do you know your backups work?**
I restore them. The restore drill builds a separate cluster from Azure alone and compares row counts and a marker row written just before; it passed at 71 and 81 s. I also did an in-place rebuild after deleting the database and its volume.
*Concept:* a backup that has never been restored is a hope, not a backup. The drill tests the whole chain: credentials, storage, WAL, and the procedure.

**What are RPO and RTO here?**
RPO (recovery point objective, how much data you can lose) is 5 minutes: WAL ships when a 16 MB file fills and at least every 5 minutes, and measured shipping took 2–5 s. RTO (recovery time objective, how long until you are back) is 30 minutes for a full restore from Azure. Measured far inside both, but on small data. For a single node loss the replica takes over in about 90 s with no data loss beyond in-flight transactions.

**Your restore took 60–80 s. What would change with 50 GB of data?**
Restore time is roughly: download the base backup, write it to disk, then replay the WAL recorded since that backup. With 50 GB, the download and the SD-card writes dominate. At an assumed 50 MB/s that is about 17 minutes of download alone, and the cards write more slowly than that, so the 30-minute RTO would probably be missed. This is an estimate, not a measurement. Ways to keep it down: more frequent base backups (less WAL to replay), faster disks, and relying on the streaming replica for node loss so a full restore is only for losing both copies. The honest next step is to measure it with a larger synthetic dataset.

**Why at-least-once and not exactly-once?**
Exactly-once across a database and a queue needs a distributed transaction. The outbox gives at-least-once: the event is written in the same transaction as the change, then sent, and marked sent only after Azure accepts it, so a crash between sending and marking resends it. A receiver that keys on `event_id` gets effectively-once. The drill measured no duplicates, but receivers must still dedupe.

**Why Azure Storage Queue and not Service Bus?**
Cost and fit: Storage Queue costs cents; the Service Bus tiers with sessions and duplicate detection have a base charge. The outbox already provides order and an ID. Service Bus is the upgrade path if strict ordering or dead-lettering in the broker is needed.

### Security
**How do you handle secrets?**
Sealed Secrets: secrets are encrypted with the cluster's public key, so the encrypted file can live in Git and only the cluster can decrypt it. Credentials are scoped as tightly as the service allows (one container, add-only queue access) and expire after a year. Scripts read secrets from the clipboard after a pause and never put them on a command line, where they could show up in history or error messages. Limit: the sealing key is not backed up, so a new cluster means re-sealing (`docs/install.md`, step 7).

**How is the cluster locked down?**
Every app namespace denies all traffic by default and allows only named flows (Traefik to the app, the app to its database, database replication, Prometheus scraping). Pods run as non-root with read-only file systems under the restricted Pod Security Standard. GitHub Actions are pinned to commit SHAs so a moved tag cannot change what runs.

### Operations
**How do you know when something breaks?**
Alerts for the app, databases, certificates, backups, sync, releases and the pages themselves, each linked to a runbook section, emailed through two Alertmanagers. If Prometheus or Alertmanager is down, or the whole site loses power or internet, the always-firing `Watchdog` alert stops pinging healthchecks.io, and it emails instead. Measured: that email arrived 6 minutes after the node holding Prometheus was powered off.
*Concept:* a dead man's switch alerts on the absence of a signal, which is the only way a monitoring system can report its own failure.

**Walk me through what happens, second by second, when a node loses power.** (From the 11-27 drill on the node holding both database primaries.)
| Time | What happens |
|---|---|
| 0 s | Database connections to the primary fail; the web pods' readiness check needs the database, so they report not ready and the sites return 503. |
| about 40–50 s | Kubernetes marks the node unreachable and its pods not ready; they leave the Services. |
| 78–89 s | The database operator promotes the replica and moves the `-rw` Service to it. |
| 93 s | The web pods are ready again; the sites serve. |
| 5 min | Kubernetes evicts the node's pods and starts them elsewhere; the burst of writes made k3s restart on the other servers, giving a 36 s site gap. |
| about 6 min | healthchecks.io emails that alerting is down (Prometheus was on the lost node). |
| after power-on | The node is Ready in under a minute; the old primary rejoins as a replica. Pods are not moved back. |

**Why did the Alertmanager pod not move when its node died, when the web pod did?**
Alertmanager runs as a StatefulSet. A StatefulSet guarantees at most one pod per identity (`alertmanager-0`), so it will not start a replacement while the old pod might still be running on a node it cannot reach; that would risk two copies writing the same data. A Deployment's pods are interchangeable, so after the 5-minute eviction it simply starts new ones elsewhere. The fix was to run two Alertmanager replicas on different nodes, not to force the move.

**Why does a retry in Traefik not risk creating a shipment twice here, and when could it?**
Traefik retries only when it got no response at all, such as a connection that failed or timed out (2 s connect timeout), which almost always means the request never reached the app. The rare risk is a pod dying after doing the work but before answering. Here that is harmless: creating a shipment twice fails on its unique tracking number, and a repeated status change is refused because the status already moved (`charts/larkspur-track/templates/traefik.yaml`). For something like a payment, retries need an idempotency key so the server can recognise a repeat.

**What went wrong during the project, and how did you catch it?**
Mistakes in the drill scripts and in my first hypotheses: an unguarded empty Prometheus result, a timeout clock started before a slow step instead of after it, a PowerShell variable `$node` silently overwriting the parameter `$Node` (names are case-insensitive), a rollout check that passed against the old deployment before Argo CD had applied the new one, and predictions that the logs disproved ("the API will keep working", "the restarts are caused by the join address"). Each was caught because the results file was incomplete or the evidence did not match, and nothing was called done until the measurement passed. The habit: state a prediction, run the drill, read the logs, and say plainly when the prediction was wrong.

**What would you change for real production?**
SSDs (or managed storage) for etcd and the databases, a second Prometheus or a hosted metrics backend, a backed-up sealing key or an external secret store, SSO instead of local accounts, a receiver for the queue, an on-call channel besides email, a UPS, and running the install guide end to end on spare hardware.
