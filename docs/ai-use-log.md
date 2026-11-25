# AI use log

Records how AI assistance (Claude) was used in this project. Decisions, verification, and commits are Jeff's.

| Date | Module | AI assistance | Jeff's work |
|---|---|---|---|
| 11-23/24-2026 | Foundation | Step-by-step guidance for switch, Pi, and k3s setup; troubleshooting support | Hardware build; ran and verified every step; diagnosed the Pi boot fault (case pressing the power button) |
| 11-24-2026 | Foundation | Drafted plan, ADR-0001, README, repository skeleton | Chose scope and stack, approved the plan, reviewed and committed |
| 11-24-2026 | Infrastructure | Designed the Ansible layout and roles; wrote the playbooks; diagnosed the group_vars location bug | Installed WSL2 + Ubuntu 26.04; created and authorized the Ansible key; ran lint, dry run, apply and idempotency checks; verified SSH hardening |
| 11-24-2026 | Platform | Designed the Argo CD app-of-apps, the cert-manager CA chain, namespaces, RBAC, network policies and the test page; diagnosed kube-router's reject behaviour | Ran every install and test; trusted the CA on the PC; verified pod security, RBAC and default-deny |
| 11-24-2026 | Application | Wrote the FastAPI app, models, migrations, templates and tests; found the missing database connect timeout behind a 130 s test | Approved the design; ran the app and the tests; did the manual checks (validation, duplicates, two-tab conflict); committed |
| 11-24-2026 | CI/CD | Wrote the workflows, Dockerfile, Helm chart, Argo CD apps, CloudNativePG setup and network policies; traced the Trivy findings to pip's vendored packages, the kube-router start-up window and the rollout 502s | Chose the options (prod database now, hostnames); ran every block, PR and merge; set the ruleset and package visibility; ran the promotion and the rollback drill |
| 11-24-2026 | Application (sign-in) | Designed and wrote sign-in, sessions, CSRF, roles, user administration and the audit log with their tests; checked framework and k3s behaviour against source; diagnosed the lost client IP (ServiceLB source NAT) | Approved the plan and each PR; committed every change; ran the promotions; created the admins with his own passwords; checked dev in the browser |
| 11-25-2026 | Operations (monitoring) | Designed and wrote the monitoring stack, app metrics, ServiceMonitor/PodMonitor and network policies, alert rules, the sealed Gmail configuration, blackbox page checks, SLO recording and burn-rate rules, the Larkspur Track dashboard, ADR-0007 and the alerts runbook; tested rules with promtool unit tests and rendered charts against their sources; diagnosed the empty Grafana password, the image smoke-test failure and the blank availability SLO | Chose email alerting and the SLO targets; created the Grafana and Gmail app passwords in Bitwarden and sealed them; ran every block, PR and merge; ran the database-outage drill and confirmed the FIRING and RESOLVED emails; checked Grafana and the dashboard |

Before UNCG coursework use: confirm the instructor's AI policy (UNCG guidance: https://ai.uncg.edu/governance/permissible-use-cases/).
