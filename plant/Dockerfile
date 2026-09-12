# syntax=docker/dockerfile:1
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.13@sha256:841c8e6fe30a8b07b4478d12d0c608cba6de66102d29d65d1cc423af86051563
# Handbook §7 says trixie for Python. Bookworm is a deliberate hold, to be revisited as
# one decision before more images inherit it.
ARG BASE=debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171

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
COPY simulator/pyproject.toml simulator/
COPY inspection/pyproject.toml inspection/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-workspace --package "${PACKAGE}"

# 3. source last
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package "${PACKAGE}"

FROM ${BASE} AS runtime
# The runtime stage is a fresh base, so it carries no CA bundle of its own; without one every
# outbound TLS call from a service in this image fails certificate verification.
RUN apt-get update \
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
