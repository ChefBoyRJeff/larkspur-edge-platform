# ADR-0001: Application stack for Larkspur Track

- **Status:** Accepted
- **Date:** 11-24-2026

## Context
Larkspur Track must run on arm64 Raspberry Pis and on amd64 AKS, be easy to read and defend in reviews and presentations, and stay small enough for a 2-node edge cluster. It is a standalone project and must not share a stack or code with other projects.

## Decision
Python with FastAPI, server-rendered HTML templates (Jinja2), PostgreSQL, SQLAlchemy, and Alembic migrations.

## Alternatives considered
- **C# / ASP.NET Core:** strong Entra ID integration; larger images; heavier for the edge.
- **Next.js / TypeScript:** rejected to keep this project fully independent of other work.

## Consequences
- Small multi-arch images; one language for app, tests, and tooling.
- Python is widely used in cloud and operations roles.
- No JavaScript framework: limited client-side interactivity (acceptable for warehouse forms).
