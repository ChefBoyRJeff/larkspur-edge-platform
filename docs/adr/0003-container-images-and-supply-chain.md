# ADR-0003: Container images and CI supply chain

- **Status:** Accepted
- **Date:** 11-24-2026

## Context
Larkspur Track runs on arm64 Raspberry Pis and must also run on amd64 (CI and a future cloud deployment). Images are built only in GitHub Actions; the admin PC has no container runtime. In March 2026, attackers force-pushed 76 of 77 version tags of `aquasecurity/trivy-action` to credential-stealing code (CVE-2026-33634), so any workflow that referenced the action by tag ran the malware.

## Decision
- **Multi-stage Dockerfile** (`app/Dockerfile`): uv installs the locked dependencies into a virtual environment in a build stage; the runtime stage (`python:3.14-slim-trixie`) contains only that environment, the migrations and a non-root user (uid 10001). It runs with a read-only root filesystem.
- **Native multi-arch builds:** one job on `ubuntu-24.04` (amd64) and one on `ubuntu-24.04-arm` (arm64), free for public repositories, instead of QEMU emulation. Each is smoke-tested (`/healthz`, non-root, read-only) and scanned by Trivy (fail on fixable HIGH or CRITICAL). On `main` and `v*` tags, both are pushed by digest and joined into one multi-arch image on GHCR (`ghcr.io/chefboyrjeff/larkspur-track`), tagged `sha-<commit>`, `main` and the release version.
- **Every third-party action is pinned to a full commit SHA**, with the version as a comment. Tags can be moved; commits cannot. Dependabot proposes weekly updates for actions, Python dependencies and base images.

## Alternatives considered
- **QEMU emulation for arm64 on an amd64 runner:** simpler workflow, but much slower builds.
- **Tag pinning (`@v7`):** readable, but trusts that the tag is never moved.
- **Docker Hub:** separate account and rate limits; GHCR is integrated with the repository's permissions.

## Consequences
- An image is published only after its smoke test and scan pass on both architectures.
- Pinned SHAs need updating; Dependabot opens those pull requests, and they go through the same checks.
- The base image is pinned by tag, not digest; Dependabot's docker updates and Trivy's scan cover drift until digest pinning is added.
