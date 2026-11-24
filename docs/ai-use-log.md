# AI use log

Records how AI assistance (Claude) was used in this project. Decisions, verification, and commits are Jeff's.

| Date | Milestone | AI assistance | Jeff's work |
|---|---|---|---|
| 11-23/24-2026 | Phase 0/1 | Step-by-step guidance and command blocks for switch, Pi, and k3s setup; troubleshooting support | Hardware build, executed every step, diagnosed the Pi boot fault (case pressing the power button), verified results |
| 11-24-2026 | M0 | Drafted plan, ADR-0001, README, repo skeleton | Chose scope and stack, approved plan, reviewed and committed |

Before UNCG coursework use: confirm the instructor's AI policy (UNCG guidance: https://ai.uncg.edu/governance/permissible-use-cases/).
| 11-24-2026 | M1 | Designed Ansible layout and roles; wrote playbooks; diagnosed RCA-001 (group_vars location) | Installed WSL2 + Ubuntu 26.04, created and authorized the Ansible key, ran lint, dry run, apply and idempotency tests, verified SSH hardening |
