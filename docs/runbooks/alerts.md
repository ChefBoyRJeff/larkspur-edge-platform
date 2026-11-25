# Runbook: Alerts and monitoring

What each alert means and what to do, plus how to look after the monitoring itself. The design is in ADR-0007. Every alert email links to its section here.

**Severity:** critical means look now; warning means look today. A critical alert silences the warning with the same name and namespace, so only the more serious one is emailed.

## Where to look
| What | Where |
|---|---|
| Dashboards (start with **Larkspur Track**) | https://grafana.larkspur.home.arpa (user `admin`, password in Bitwarden "Larkspur Grafana admin") |
| Prometheus: targets, rules, queries | http://localhost:9090 after the port-forward below |
| Alertmanager: active alerts, silences | http://localhost:9093 after the port-forward below |
| Argo CD: what is deployed | https://localhost:8080 after the port-forward below (its own certificate, so the browser warns) |

Port-forwards (PowerShell, in a terminal you leave open; Ctrl+C stops one):
```powershell
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
kubectl port-forward -n monitoring svc/kube-prometheus-stack-alertmanager 9093:9093
kubectl port-forward -n argocd svc/argocd-server 8080:443
```

Commands below use prod; for dev, replace `larkspur-prod` with `larkspur-dev`.

## Silence an alert during planned work
Alertmanager (http://localhost:9093) → **New Silence** → matchers such as `namespace="larkspur-dev"` and `alertname="LarkspurTrackUnavailable"` → a duration and a comment → **Create**. Silences expire by themselves; delete one early from the **Silences** tab. Never silence a whole severity.

---

## Larkspur Track

### LarkspurTrackDown
**Meaning:** no web pod of the environment has answered a Prometheus scrape for 2 minutes: they crashed, are not running, or the metrics port is unreachable.
**Check:**
```powershell
kubectl get pods -n larkspur-prod -l app.kubernetes.io/name=larkspur-track
kubectl describe pod -n larkspur-prod -l app.kubernetes.io/name=larkspur-track | Select-String -Pattern 'Reason|Message|Last State' -Context 0,1
kubectl logs -n larkspur-prod deploy/larkspur-track -c web --tail=50 --previous
```
**Fix:** CrashLoopBackOff after a release → roll back (`docs/runbooks/release-and-rollback.md`). Pending pods → check the nodes (`kubectl get nodes`). Pods running but the alert firing → check that `allow-prometheus-to-larkspur-track-metrics` exists (`kubectl get networkpolicy -n larkspur-prod`).

### LarkspurTrackUnavailable
**Meaning:** pods may be running, but none has been ready for 2 minutes. Readiness (`/readyz`) needs the database, so this is usually a database problem.
**Check:** look for `LarkspurDatabaseDown` first, then:
```powershell
kubectl get cluster larkspur-db -n larkspur-prod
kubectl get pods -n larkspur-prod
kubectl logs -n larkspur-prod deploy/larkspur-track -c web --tail=50
```
**Fix:** follow `LarkspurDatabaseDown` if it is firing. Otherwise the logs show the startup or connection error. Measured in a drill: this alert fires about 5 minutes 30 seconds after the database stops, and clears within a minute of recovery.

### LarkspurTrackHighErrorRate
**Meaning:** over 5% of real requests (health checks excluded) have returned 5xx for 10 minutes.
**Check:** the Larkspur Track dashboard's error panels; which routes fail:
```promql
sum by (route, status) (rate(larkspur_http_requests_total{namespace="larkspur-prod", status=~"5.."}[10m]))
```
then `kubectl logs -n larkspur-prod deploy/larkspur-track -c web --tail=200` for the tracebacks.
**Fix:** started with a release → roll back. Database errors in the logs → see the database alerts.

### LarkspurTrackSlow
**Meaning:** the slowest 5% of real requests have taken over 1 second for 15 minutes.
**Check:** the dashboard's response-time panel; slow routes:
```promql
histogram_quantile(0.95, sum by (route, le) (rate(larkspur_http_request_duration_seconds_bucket{namespace="larkspur-prod"}[15m])))
```
Node load: Grafana **Node Exporter / Nodes**; database connections: `LarkspurDatabaseNearConnectionLimit`.
**Fix:** a node short of CPU or memory → find what grew (Grafana **Kubernetes / Compute Resources / Cluster**). One slow route after a release → roll back and fix the query.

### LarkspurTrackManyFailedSignIns
**Meaning:** over 20 failed sign-ins in 15 minutes: someone may be guessing passwords. Accounts lock after 5 wrong passwords.
**Check:** **Audit** page (`/audit`, filter `sign_in_failed`): which usernames and which addresses.
**Fix:** one address trying many usernames → find that device on the network. A real user locked out → reset or unlock them (`docs/runbooks/user-administration.md`).

### LarkspurTrackErrorBudgetBurn
**Meaning:** prod is failing requests fast enough to use up the weekly availability budget (0.5% of requests): 10% of it within 1 hour or 25% within 6 hours (critical), or 50% within 1 day (warning). The `window` label says which.
**Check:** the dashboard's **Prod SLOs** row (budget left) and error panels; the routes failing, as in `LarkspurTrackHighErrorRate`.
**Fix:** as for `LarkspurTrackHighErrorRate`. Afterwards, if the budget is below 0%, hold non-urgent releases to prod until it recovers.

### LarkspurTrackLatencyBudgetBurn
**Meaning:** as above, for the latency SLO: too many requests take over 1 second (budget 1% of requests).
**Check and fix:** as for `LarkspurTrackSlow`.

## Database

### LarkspurDatabaseDown
**Meaning:** PostgreSQL has not reported up for 2 minutes: the pod is gone, PostgreSQL stopped, or Prometheus cannot reach the metrics port.
**Check:**
```powershell
kubectl get cluster larkspur-db -n larkspur-prod
kubectl get pods -n larkspur-prod -l cnpg.io/cluster=larkspur-db -o wide
kubectl get cluster larkspur-db -n larkspur-prod -o jsonpath='{.metadata.annotations}'
kubectl logs -n larkspur-prod larkspur-db-1 --tail=50
```
**Fix:**
- A `cnpg.io/hibernation: on` annotation (left from a drill) → `kubectl annotate cluster larkspur-db -n larkspur-prod --overwrite cnpg.io/hibernation=off`.
- The pod is Pending → its node is down; the data lives on that node (local-path), so the database returns when the node does (`docs/runbooks/replace-a-node.md` if it cannot).
- The database is up but the alert fires → check `allow-prometheus-to-larkspur-db-metrics` (`kubectl get networkpolicy -n larkspur-prod`).

### LarkspurDatabaseNearConnectionLimit
**Meaning:** over 80% of `max_connections` (100) have been in use for 5 minutes. New connections will soon be refused.
**Check:** who holds them:
```promql
sum by (application_name, state) (cnpg_backends_total{namespace="larkspur-prod"})
```
**Fix:** many `idle in transaction` → an app bug holding transactions open; restart the web pods (`kubectl rollout restart deployment/larkspur-track -n larkspur-prod`) and fix the code. Growth with traffic → reduce the app's connections (fewer pods or a smaller connection pool) or raise `max_connections` in the Cluster's `postgresql.parameters`.

### LarkspurDatabaseLarge
**Meaning:** the `larkspur` database is over 1.5 GiB, 75% of the 2 GiB planned for its volume. local-path does not enforce the size, so the database could keep growing until the node's SD card is full.
**Check:** Grafana **Node Exporter / Nodes** for the node's free disk; which tables grew (`kubectl exec -it -n larkspur-prod larkspur-db-1 -- psql -U postgres -d larkspur -c "\dt+"`).
**Fix:** expected growth → plan a bigger volume or the USB SSD upgrade (plan.md, risks). Unexpected growth → find the table and the cause before deleting anything.

### LarkspurDatabaseMetricsFailing
**Meaning:** CloudNativePG could not query PostgreSQL for its metrics for 10 minutes; database and backup alerts may be blind.
**Check:** `kubectl logs -n larkspur-prod larkspur-db-1 --tail=100 | Select-String -Pattern 'error' -SimpleMatch`
**Fix:** usually PostgreSQL itself is unhealthy (see `LarkspurDatabaseDown`) or a custom metrics query broke after an operator upgrade.

## Backups
Each database archives its WAL to Azure Blob Storage continuously and takes a base backup every night at 08:00 UTC (Data module, ADR-0008). Restores: `docs/runbooks/backups.md`.

### LarkspurWalArchivingFailing
**Meaning:** a WAL file failed to reach Azure in the last 10 minutes and none has succeeded since. Changes since the last successful archive exist only on the node's SD card, and PostgreSQL keeps the unarchived WAL, so its disk use grows.
**Check:**
```powershell
kubectl logs -n larkspur-prod larkspur-db-1 -c plugin-barman-cloud --tail=50
kubectl get objectstore larkspur-db-backup -n larkspur-prod -o yaml
```
`AuthenticationFailed` or `403` → the SAS token expired or its access policy was removed; a connection error → the node's internet connection or `allow-larkspur-db-to-azure-blob`.
**Fix:** credential problems → create a new one (`docs/runbooks/backups.md`, "Rotate the credential"). Network problems → once the connection returns, PostgreSQL archives the backlog by itself; check that the alert clears.

### LarkspurBackupTooOld
**Meaning:** the newest base backup in Azure is more than 26 hours old, or there is none: last night's backup failed.
**Check:**
```powershell
kubectl get backups -n larkspur-prod --sort-by=.metadata.creationTimestamp
kubectl describe backup -n larkspur-prod <newest backup>
```
**Fix:** the backup's events and the plugin log (above) give the cause, usually the same as for WAL archiving. To take a backup now: `docs/runbooks/backups.md`, "Take a backup now".

## Certificates
Certificates last 90 days and renew 15 days before expiry; the root CA lasts 10 years.

### CertManagerDown
**Meaning:** Prometheus has not reached the cert-manager controller for 10 minutes. Nothing would renew certificates.
**Check:** `kubectl get pods -n cert-manager`; `kubectl logs -n cert-manager deploy/cert-manager --tail=50`; the `cert-manager` app in Argo CD.
**Fix:** a crashing pod → its logs; a failed sync → the Argo CD app's events.

### CertificateNotReady
**Meaning:** a certificate (named in the alert) has been in a failed state for 15 minutes.
**Check:**
```powershell
kubectl get certificates -A
kubectl describe certificate <name> -n <namespace>
kubectl get certificaterequests -n <namespace>
```
**Fix:** the events name the cause, usually the `larkspur-ca` issuer not being ready (`kubectl get clusterissuer`) or an invalid name in the Certificate.

### CertificateRenewalOverdue
**Meaning:** a certificate's renewal time passed over an hour ago and it has not renewed. It still has about two weeks before it expires.
**Check and fix:** as for `CertificateNotReady`, plus the cert-manager logs. cert-manager retries failed renewals with a growing delay of up to an hour; once the cause is fixed, `cmctl renew <name> -n <namespace>` (cert-manager's CLI, not installed by default) retries at once.

### CertificateExpiringSoon
**Meaning:** a certificate expires within 7 days: renewal has been failing for over a week. When it expires, browsers reject the site.
**Fix:** as for `CertificateNotReady`, now. If the alert names `larkspur-root-ca` (10 years, renewed 30 days before expiry), plan a CA rotation: every certificate is reissued from the new CA, and every PC that trusts the old one must trust the new one.

## Page checks (blackbox exporter)
The blackbox exporter loads `https://track.larkspur.home.arpa/readyz`, `https://track-dev.larkspur.home.arpa/readyz` and `https://grafana.larkspur.home.arpa/api/health` every 30 s through Traefik on lark-k3s-01, trusting only the Larkspur root CA.

### LarkspurEndpointDown
**Meaning:** the page in the `target` label has not answered 200 over trusted HTTPS for 3 minutes. Critical for prod, warning otherwise.
**Check:** what the check saw:
```promql
probe_http_status_code{target="track-prod"}
```
`0` means no HTTP answer at all (TLS, connection or timeout); any other number is the page's own answer. Then compare with the app alerts: if only this one fires, the problem is between the user and the app (Traefik, the certificate, lark-k3s-01).
```powershell
kubectl get pods -n kube-system -l app.kubernetes.io/name=traefik -o wide
kubectl get nodes
```
**Fix:** Traefik down → its pod's logs; lark-k3s-01 down → `docs/runbooks/replace-a-node.md`; certificate errors → the certificate alerts.

### LarkspurEndpointSlow
**Meaning:** the page has taken over 2 seconds to answer for 15 minutes.
**Check and fix:** as for `LarkspurTrackSlow`; if the app's own latency is normal, look at Traefik and the node.

### BlackboxProbesMissing
**Meaning:** fewer than 3 page checks have reported for 5 minutes, so `LarkspurEndpointDown` cannot fire.
**Check:** `kubectl get pods -n monitoring -l app.kubernetes.io/name=prometheus-blackbox-exporter`; the `blackbox-exporter` app in Argo CD; Prometheus **Targets** for the `probe/...` jobs.
**Fix:** the exporter waiting for its CA file → `kubectl get certificate blackbox-exporter-ca -n monitoring`. After adding or removing a page check, update the count of 3 in `platform/config/monitoring/blackbox-rules.yaml`.

## Alerts from the chart's default rules
kube-prometheus-stack also brings general rules (for example `KubePodCrashLooping`, `NodeFilesystemSpaceFillingUp`, `TargetDown`). Their emails link to the upstream runbooks at https://runbooks.prometheus-operator.dev. `Watchdog` always fires on purpose, to prove alerting works, and is never emailed.

---

## Change the alert email settings
The Alertmanager configuration holds the Gmail address and an app password, so it is sealed. Needed when the address or app password changes.
1. In the Google account, with 2-step verification on: **Security → App passwords** → create one named "Larkspur alerts". Save it in Bitwarden.
2. Optional: change routing or receivers in `platform/alertmanager/alertmanager.yaml.tmpl`, keeping the `@@GMAIL_ADDRESS@@` and `@@GMAIL_APP_PASSWORD@@` placeholders.
3. PowerShell, from the repo root:
   ```powershell
   ./platform/alertmanager/seal-config.ps1
   ```
   Type the address; when asked, copy the app password and press Enter. The script checks the password's shape, seals the filled-in configuration into `platform/config/monitoring/alertmanager-config.sealedsecret.yaml` and checks the cluster can decrypt it. The plain configuration is never written to the repository. kubeseal is in `%LOCALAPPDATA%\Programs\kubeseal`.
4. Commit the sealed file in a pull request and merge. Alertmanager reloads the configuration within a few minutes.
5. Test: send a test alert to Alertmanager and wait for its FIRING email:
   ```powershell
   $body = ConvertTo-Json -Depth 4 @(@{ labels = @{ alertname = 'LarkspurAlertTest'; severity = 'warning'; namespace = 'monitoring' }; annotations = @{ summary = 'Test alert: ignore it.' } })
   Invoke-RestMethod -Method Post -ContentType 'application/json' -Body $body http://localhost:9093/api/v2/alerts
   ```
   (with the Alertmanager port-forward running). It resolves by itself after 5 minutes, followed by a RESOLVED email.

## Change the Grafana admin password
The password comes from the SealedSecret `grafana-admin` (keys `admin-user`, `admin-password`). Grafana keeps no data, so it reads the password again at every start.
1. Generate the new password in Bitwarden ("Larkspur Grafana admin") and copy it.
2. PowerShell, from the repo root. The clipboard is read only after the pause, because copying these commands overwrites it:
   ```powershell
   Read-Host 'Copy the new Grafana password from Bitwarden, then press Enter' | Out-Null
   $pw = (Get-Clipboard -Raw).Trim(); Set-Clipboard -Value ' '
   if ($pw.Length -lt 16) { throw "clipboard holds $($pw.Length) characters; copy the password again" }
   $dir = New-Item -ItemType Directory (Join-Path $env:TEMP ([guid]::NewGuid()))
   try {
     [IO.File]::WriteAllText((Join-Path $dir.FullName 'admin-user'), 'admin'); [IO.File]::WriteAllText((Join-Path $dir.FullName 'admin-password'), $pw)
     kubectl create secret generic grafana-admin -n monitoring --from-file=$($dir.FullName) --dry-run=client -o json |
       & "$env:LOCALAPPDATA\Programs\kubeseal\kubeseal.exe" --format yaml --controller-name sealed-secrets-controller --controller-namespace kube-system |
       Out-File -Encoding utf8 platform/config/monitoring/grafana-admin.sealedsecret.yaml
   } finally { Remove-Item -Recurse -Force $dir.FullName; Remove-Variable pw }
   ```
3. Commit the sealed file in a pull request and merge. After Argo CD syncs, restart Grafana so it reads the new password: `kubectl rollout restart deployment/kube-prometheus-stack-grafana -n monitoring`.
4. Sign in with the new password; `admin`/`admin` must fail.

## Change the Larkspur Track dashboard
Edits made in Grafana are lost when Grafana restarts. Change `platform/config/monitoring/larkspur-dashboard.yaml` instead: to start from an edited copy, use the dashboard's **Export → Export as JSON** in Grafana, put the JSON into the ConfigMap, keep `"uid": "larkspur-track"`, and open a pull request. The sidecar loads the new version within a minute of the sync.

## Add an alert rule
Add it to the PrometheusRule next to what it watches (the chart for the app, `platform/config/databases/` for databases, `platform/config/monitoring/` for the rest), with `severity`, a `summary`, a `description` and `runbook: docs/runbooks/alerts.md#<alert name in lower case>`, then add its section to this runbook. Check it with `promtool check rules` and a `promtool test rules` unit test before the pull request.
