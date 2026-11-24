# ADR-0004: Packaging and deploying Larkspur Track

- **Status:** Accepted
- **Date:** 11-24-2026

## Context
Larkspur Track needs the same manifests in dev and prod with a few differences (image tag, hostname, later replica counts). Argo CD already deploys the platform from this repository. Schema changes (Alembic) must be applied before new code serves traffic, and every namespace denies all traffic by default.

## Decision
- **A Helm chart in this repository** (`charts/larkspur-track`): `values.yaml` holds shared defaults; `values-<env>.yaml` sets the image tag and hostname. Argo CD renders it (release name `larkspur-track`) with the Helm version it bundles; CI runs `helm lint` and `helm template` with that same version.
- **Immutable image tags:** each environment names a `sha-<commit>` tag. Releasing to an environment is a pull request that changes that tag; rolling back is reverting it.
- **Migrations as an Argo CD Sync hook**, ordered by sync waves: network policies (wave -1), then the `alembic upgrade head` job (wave 0), then the Deployment (wave 1). A failed migration stops the sync before any new pod starts, and the running release keeps serving.
- **The database is not part of the chart.** The CloudNativePG cluster lives in `platform/config/databases/`, so removing or reinstalling the app never touches its data.
- **Pods:** restricted security context, read-only root filesystem, no service account token, readiness on `/readyz` (database reachable), liveness on `/healthz` (process alive), rolling updates with no unavailable pods.

## Alternatives considered
- **Kustomize overlays:** no templating to learn, but per-environment patches get harder to read than a short values file.
- **Plain manifests per environment:** simplest, but the copies drift.
- **PreSync hook for migrations:** runs before every Sync-phase resource, including this chart's network policies, so a first install would start the job before its egress rule exists.
- **Migrations at container start:** every replica would race to migrate, and a bad migration would crash-loop the new release.

## Consequences
- A release is a reviewed one-line change, and the rollout and its schema change succeed or fail together.
- Migrations must stay backward compatible with the release still running (expand first, contract in a later release).
- The chart hard-codes the `app.kubernetes.io/name: larkspur-track` label, because the database network policy admits pods by that label.
