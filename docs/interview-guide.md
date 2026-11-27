# Interview guide

How to present Larkspur Edge in a technical interview: what it is, how to walk through it, the problems worth telling, and answers to likely questions. Every number here comes from a drill or check that was actually run; the pull request numbers point to the evidence.

**How to describe it honestly:** a personal project, built to production practices on my own hardware, over five days in November 2026. It is not professional production experience, and nobody depends on it. I built it with an AI coding assistant (Claude): the assistant wrote most of the code and proposed designs; I chose the scope, approved each design and cost, ran every command, deployment and drill, merged every pull request, and checked the results. The split is recorded in `docs/ai-use-log.md`. Say this plainly if asked, and be ready to explain any part in your own words.

## The 30-second version
Larkspur Track is a shipment-tracking app for a fictional warehouse that must keep working when its internet link is down. It runs on a two-node Kubernetes cluster (k3s) on Raspberry Pi 5s. Everything is in Git and deployed by Argo CD. The databases back up to Azure Blob Storage, and shipment events sync to an Azure queue when the link is up. I proved the recovery paths with drills: a bad release, losing the database, losing a node, and cutting the internet link. Each drill found something I then fixed.

## 3–5 minute walkthrough
1. **The problem (30 s).** A warehouse needs tracking that does not stop when the WAN goes down, and the company needs the data in Azure. So the app runs on site, and Azure is for backups and sync, not for serving users.
2. **The platform (1 min).** Two Pi 5s run k3s: `lark-k3s-01` is the control plane and holds the data, `lark-k3s-02` is a worker. Ansible builds the nodes. Argo CD syncs everything from `main` (app of apps); changes happen only by pull request. cert-manager runs an internal CA for HTTPS, Sealed Secrets keeps secrets encrypted in Git, and every app namespace is default-deny with restricted pod security.
3. **The app and delivery (1 min).** FastAPI with server-rendered pages and PostgreSQL (CloudNativePG), with sign-in, three roles and an append-only audit log. GitHub Actions tests every pull request, builds amd64 and arm64 images, and scans them with Trivy. Releasing is a pull request that changes an immutable image tag; rolling back is reverting it.
4. **Operations (1 min).** Prometheus, Grafana and Alertmanager, with alerts emailed and each one linked to a runbook section. 7-day SLOs for prod (99.5% of requests succeed, 99% under 1 s) with burn-rate alerts. A blackbox exporter checks the pages the way a browser does.
5. **Resilience (1 min).** WAL archiving and nightly backups to Azure (target: lose at most 5 minutes, back within 30). A transactional outbox and sync worker send events to Azure Storage Queue at least once. Then the drills and what they changed (below).

## 10-minute deep dive (pick by audience)
- **GitOps and release safety:** sync waves (network policies, then the migration hook, then the Deployment), so a failed migration stops a release before any pod changes. The bad-release drill showed Argo CD will not start a revert's sync while a stuck one is retrying; fixed with `retry.refresh`, a CI check that the image tag exists, and a `LarkspurReleaseStuck` alert (#67–#69).
- **Data protection:** the Barman Cloud plugin archives WAL to a per-environment container with a SAS token for that container only. Measured: WAL reaches Azure in 2–5 s; restore drills rebuilt a database in 71–81 s with every row and a marker present (#56, #57). The full-loss drill rebuilt dev in place from Azure in 60 s (#70) and showed the app needs a restart because the rebuilt database gets a new password.
- **Offline-first sync:** the change and its outbox row commit in one transaction; one worker sends oldest-first and stops at the first failure, so order holds; `event_id` makes duplicates harmless. Measured: 7 of 7 events made during a 48 s outage, with a worker restart in the middle, reached Azure 5 s after the link returned, no duplicates (#65).
- **Security:** least-privilege credentials (backup token per container; sync token can only add messages, tested to fail on read and on the other queue), default-deny network policies, non-root read-only containers, actions pinned to commit SHAs, no plain secrets in Git.
- **Observability:** SLO burn rates (16.8x / 7x / 3.5x) with a minimum request count, so a quiet dev cluster does not page; promtool unit tests for alert rules.

## Problems worth telling (situation, action, result)
| Story | What happened | What I did | Result |
|---|---|---|---|
| A leaked token | While sealing the backup credential, a PowerShell `Set-Content` call treated the SAS token as a file path and printed it in its error, in the terminal and in the AI chat | Revoked it at once by deleting its access policy, then hardened the script: timestamped policies, revoke-older, tests over REST, no token on any command line, masked errors (#54) | No use of the token was possible after revocation; the pattern was removed from the runbooks too (#55) |
| A dashboard that went blank | The availability SLO showed "no data" whenever there were no errors, because the error series did not exist | Rewrote the ratio as `(errors or 0 * total) / total` and added a regression test (#51) | 7-day availability reads 100% instead of blank |
| A stuck release | A release with a missing image left the migration job pulling forever; users saw nothing (593 of 593 requests fine) and Argo CD said Healthy, so nothing alerted, and a revert did not apply | Predicted the behaviour from Argo CD's source, confirmed it in the drill, stopped the stuck sync, then fixed the three gaps (#67–#69) | Reverts are picked up on the next retry, bad tags fail CI, and a stuck release alerts within 5 minutes |
| Losing a node | Powering off the worker caused about 50 s of failed requests (8 in prod, 7 in dev) and stopped alert email for 8 minutes, because the only Alertmanager was stuck on that node | Two Alertmanagers on different nodes; a Traefik retry with a 2 s connect timeout (#73) | Re-run: 0 of 498 prod and 1 of 499 dev requests failed; alert emails arrived during the outage |
| A runbook that was wrong | The never-tested "restore after losing the database" procedure missed the app restart and had the steps in a risky order | Ran it as a drill on dev and rewrote it from what happened (#70, #71) | Measured rebuild: 60 s to a ready database, nothing lost |

## Likely questions
**Why k3s on Raspberry Pis instead of a cloud service?** The requirement is to keep working without the internet, so the system has to run on site. k3s is a certified Kubernetes in one small binary, which fits 8 GB Pis. The same Helm chart is meant to run on AKS (amd64 images are built), but I have not run it there.

**How do you deploy and roll back?** A pull request changes the image tag in `values-<env>.yaml`; Argo CD applies it after the merge. Prod only gets a tag dev is running. Rolling back is GitHub's Revert button on that pull request. Measured in the CI/CD rollback drill: about 30 s from the merged revert to the old release serving.

**What happens if a migration fails?** It runs as an Argo CD Sync hook before the new pods (sync waves), so the sync stops and the old release keeps serving. Migrations must stay backward compatible so a rollback still works.

**How do you know your backups work?** I restore them. The restore drill builds a separate cluster from Azure alone and compares row counts and a marker row written just before; it passed at 71 and 81 s. I also did an in-place rebuild after deleting the database and its volume.

**What are RPO and RTO here?** RPO 5 minutes (WAL ships when a file fills and at least every 5 minutes); RTO 30 minutes. Measured far inside both, but those are dev measurements with small data.

**Why at-least-once and not exactly-once?** Exactly-once across a database and a queue needs a distributed transaction. The outbox gives at-least-once; a receiver that keys on `event_id` gets effectively-once. The drill measured no duplicates, but receivers must still dedupe.

**Why Azure Storage Queue and not Service Bus?** Cost and fit: Storage Queue costs cents; the Service Bus tiers with sessions and duplicate detection have a base charge. The outbox already provides order and an ID. Service Bus is the upgrade path if strict ordering is ever needed.

**How do you handle secrets?** Sealed Secrets: only the cluster can decrypt what is in Git. Credentials are scoped as tightly as the service allows and expire after a year. Limit: the sealing key is not backed up, so a new cluster means re-sealing (`docs/install.md`, step 7).

**What is the biggest weakness?** `lark-k3s-01` is a single point of failure: it holds the control plane and every database volume. Losing it means rebuilding the databases from Azure. A third node and replicated storage would fix it.

**How do you know when something breaks?** Alerts for the app, databases, certificates, backups, sync, releases and the pages themselves, each linked to a runbook section; email through two Alertmanagers. The drills checked that the alerts fire, and found the gap when they did not.

**What would you change for real production?** Three nodes with replicated storage (or managed PostgreSQL), a backed-up sealing key or an external secret store, SSO instead of local accounts, a receiver for the queue, an on-call channel besides email, and running the install guide end to end on spare hardware.

## Follow-up questions to practise
- Walk me through what Kubernetes does, second by second, when a node loses power. (The node-loss table in `docs/runbooks/replace-a-node.md`.)
- Why did the Alertmanager pod not move when the node died, when the web pod did? (StatefulSet pods are not replaced while the old one might still be running.)
- Your restore took 60 s. What would change with 50 GB of data?
- Why does a retry in Traefik not risk charging a customer twice here, and when could it? (`charts/larkspur-track/templates/traefik.yaml`.)
- What did the AI get wrong, and how did you catch it? (Examples: an unguarded empty query result, a timeout measured from the wrong moment, a case-insensitive variable clash; each was caught because the results file was incomplete or wrong, and nothing was claimed until it passed.)
