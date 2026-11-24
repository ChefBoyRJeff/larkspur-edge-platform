# syntax=docker/dockerfile:1
# Larkspur Track image. Built in CI for linux/amd64 and linux/arm64 (see .github/workflows/image.yml).

FROM ghcr.io/astral-sh/uv:0.13.9 AS uv

# --- build stage: resolve and install locked dependencies into /app/.venv ---
FROM python:3.14-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first, so this layer is reused until uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable
# Fail the build if the templates did not make it into the installed package.
RUN test -f /app/.venv/lib/python3.14/site-packages/larkspur_track/templates/base.html

# --- runtime stage: no uv, no build tools, non-root ---
FROM python:3.14-slim-trixie AS runtime
RUN groupadd --system --gid 10001 larkspur \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin larkspur
# The base image ships pip, which carries its own copies of urllib3, msgpack and setuptools.
# The app never uses pip at runtime (uv installed everything in the build stage), so remove it.
RUN python -m pip uninstall --yes pip
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Migrations run from this image as a Kubernetes job before each release.
COPY alembic.ini ./
COPY migrations ./migrations
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
# Proxy headers are trusted because only Traefik can reach the pod (NetworkPolicy).
CMD ["uvicorn", "larkspur_track.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
