# ADR-0007: Monitoring, alerting and SLOs

- **Status:** Accepted
- **Date:** 11-25-2026

## Context
The site runs unattended in a warehouse server room on two Raspberry Pi 5s with SD cards. Someone must find out when Larkspur Track, its database or its certificates stop working, before staff do, and there must be a measurable answer to "is the service good enough?". Everything has to run on the Pis themselves, cost nothing, and be declared in Git like the rest of the platform.

## Decision
- **kube-prometheus-stack** (`platform/apps/kube-prometheus-stack.yaml`): the Prometheus Operator, Prometheus, Alertmanager, Grafana, node-exporter and kube-state-metrics in the `monitoring` namespace, with the chart's default dashboards and alert rules.
  - **Retention 7 days or 2 GB** on a 3 Gi local-path volume, to limit SD card writes. The SLO window is therefore 7 days, not the usual 30.
  - **k3s control-plane scrapes off** (etcd, scheduler, controller manager, proxy): k3s runs them inside its own process on localhost, with SQLite instead of etcd, so their targets and rules would always report down.
  - The operator's admission webhook certificate comes from cert-manager instead of Helm hook jobs, so Argo CD never sees a changing hook.
  - Prometheus selects every ServiceMonitor, PodMonitor, Probe and PrometheusRule in the cluster, so each component brings its own monitoring.
- **What is scraped, and who may scrape it:**
  - **Larkspur Track** exposes metrics with prometheus-client on a separate port, 9102, served from a background thread: requests and latency by route template (unmatched paths share one label), sign-ins by result, and shipments by status read from the database at scrape time. Every pod reports the same shipment count, so queries use `max()`. A ServiceMonitor in the chart scrapes it; a NetworkPolicy admits only Prometheus to 9102, and the public hostname never routes `/metrics`.
  - **Databases:** a PodMonitor per environment reads CloudNativePG's exporter on 9187, created by us because the operator's `enablePodMonitor` is deprecated; a NetworkPolicy admits only Prometheus.
  - **cert-manager:** the chart's ServiceMonitor, with `honorLabels` so certificate metrics keep each certificate's namespace.
  - **From the user's side:** the **blackbox exporter** loads `track`, `track-dev` and Grafana over HTTPS every 30 s, the way a browser does: same names and address as the admin PC's hosts file, through Traefik, trusting only the Larkspur root CA. This replaces Uptime Kuma from the original plan: the results land in Prometheus with everything else, use the same alert path and are declared in Git, with no separate app, database or UI to run.
- **Alerting: Alertmanager emails a Gmail address** through `smtp.gmail.com:587` with a Gmail app password. The configuration is a template (`platform/alertmanager/alertmanager.yaml.tmpl`) that `seal-config.ps1` fills in and seals, so the address and password never reach Git in plain text.
  - Alerts are grouped by namespace and alert name, sent again every 12 hours while firing, and followed by a RESOLVED email.
  - Watchdog (always firing by design), InfoInhibitor and `severity: info` alerts are not emailed. Watchdog pings a healthchecks.io check every minute instead (a dead man's switch), which emails if the pings stop.
  - A critical alert silences the warning with the same name and namespace.
  - Our rules use two severities: **critical** means someone should look now; **warning** means look today.
- **Alert rules**, each with a section in `docs/runbooks/alerts.md`:
  - App (chart, per environment): down, no ready pods, error rate over 5%, p95 over 1 s, more than 20 failed sign-ins in 15 minutes.
  - Database (per environment): down, near the connection limit, larger than planned, metric collection failing.
  - Certificates: cert-manager down, a certificate not ready, renewal overdue, expiry within 7 days.
  - Page checks: a page failing, slow, or checks not reporting.
- **SLOs for prod** (`platform/config/monitoring/larkspur-slo-rules.yaml`), over a rolling 7 days, counting real requests only (health checks excluded):
  - **Availability:** 99.5% of requests succeed (no 5xx). Error budget 0.5%, about 50 minutes of full outage a week.
  - **Latency:** 99% of requests answer within 1 second. Error budget 1%.
  - Recording rules compute the ratios for 5 minutes to 1 day every 30 s, and the 7-day totals every 5 minutes, for both environments. Only prod has an SLO; dev is shown for comparison and is broken on purpose in drills.
  - **Burn-rate alerts** (multi-window, as in Google's SRE workbook, adapted to a 7-day budget): 10% of the budget in 1 hour or 25% in 6 hours is critical; 50% in 1 day is a warning. A long and a short window must both agree, and the long window needs at least 50 real requests, so one failure in a quiet hour cannot page.
- **Grafana** at `https://grafana.larkspur.home.arpa`, with a certificate from the Larkspur CA and the admin password from a SealedSecret. The **Larkspur Track** dashboard is a ConfigMap (`platform/config/monitoring/larkspur-dashboard.yaml`) loaded by Grafana's sidecar. Grafana keeps no data of its own, so dashboards and settings live only in Git.
- **Argo CD, Prometheus and Alertmanager have no ingress.** They are reached with `kubectl port-forward`, which needs cluster credentials.

## Alternatives considered
- **Uptime Kuma** for outside-in checks: a friendly UI, but a separate app with its own SQLite database and its own notification settings, configured by clicking rather than in Git.
- **Grafana Cloud or another hosted service:** less to run, but it depends on the internet, which the site must not, and sends site data off-site.
- **Slack, Discord or Telegram notifications:** quicker to notice than email, but another account to create and maintain; email needs only an app password on an existing account.
- **30-day SLOs:** the industry default, but they would need 30 days of data on SD cards (or a second, long-term store). 7 days fits the retention we chose.
- **Alerting on raw thresholds only** (e.g. error rate over 5%): already in place for quick diagnosis, but a threshold says nothing about whether the promise to users is at risk. Burn rates tie alerts to the SLO.

## Consequences
- Installing the stack raised memory use from 34% to 47% on lark-k3s-01 and from 14% to 22% on lark-k3s-02 (about 1 GB and 0.6 GB), mostly Prometheus and Grafana. Grafana runs near its 384 MiB limit and is watched.
- Alerts depend on the cluster being up. Since 11-27-2026 the healthchecks.io dead man's switch covers that: when Prometheus, Alertmanager or the whole site stops, healthchecks.io emails within about 5 to 8 minutes. It was added after the lark-k3s-01 node-loss drill, where an 11-minute outage sent no email because the only Prometheus was on the lost node.
- The page checks go to lark-k3s-01 like the admin PC does, so losing that Pi also fails the page checks, which matches what users would see.
- A database outage sends two emails (`LarkspurDatabaseDown` and `LarkspurTrackUnavailable`); merging them would mean changing and re-sealing the Alertmanager configuration.
- A new alert rule needs a runbook section; a new page check needs the count in `BlackboxProbesMissing` updated.
- Measured in the dev drill: stopping the database fired `LarkspurTrackUnavailable` after about 5 minutes 30 seconds (readiness failures, a 30 s scrape, rule evaluation and the 2-minute `for`), and the RESOLVED email arrived about 5 minutes after recovery.
