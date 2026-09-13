# syntax=docker/dockerfile:1
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563
# Handbook §7's trixie. The bookworm hold ended the first time `make scan-images` ran and put
# a number on it: two HIGH pcre2 CVEs (CVE-2026-86145, CVE-2026-89161), fixed in Debian but not
# in any bookworm-slim tag. Trixie builds unchanged and scans clean.
ARG BASE=debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

FROM ${UV_IMAGE} AS uv

FROM ${BASE} AS build
COPY --from=uv /uv /uvx /bin/
# UV_PROJECT_ENVIRONMENT keeps the environment outside /src so the source COPY below cannot
# clobber it; only-managed forbids falling back to a system interpreter, which is what makes
# spec §10.7's "the base image does not dictate the Python version" true rather than aspirational.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/opt/venv
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src

# 1. the interpreter, pinned by .python-version
COPY .python-version ./
RUN uv python install

# 2. dependencies only — this layer survives every source edit
ARG PACKAGE
COPY pyproject.toml uv.lock ./
COPY analysis/pyproject.toml analysis/
COPY agent/pyproject.toml agent/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package "${PACKAGE}"

# 3. source last
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package "${PACKAGE}"

FROM ${BASE} AS runtime
# The runtime stage is a fresh base, so it carries no CA bundle of its own; without one every
# outbound TLS call from a service in this image fails certificate verification.
#
# `upgrade` is here because the base image lags the archive: Debian publishes a patched
# package before it rebuilds trixie-slim around it, so re-pinning the digest cannot close the
# window and the scanner is right to fail. It does make this layer depend on build date — but
# the `install` below already did, since no version is pinned. The digest pin covers the base
# layer; this layer never was reproducible.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/python /opt/python
COPY --from=build /opt/venv  /opt/venv
COPY --from=build --chown=10001:10001 /src /src
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1
WORKDIR /src
# Numeric, so an orchestrator checking runAsNonRoot can read it (docs/ENGINEERING.md §7).
USER 10001:10001
