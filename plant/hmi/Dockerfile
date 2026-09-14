# syntax=docker/dockerfile:1
ARG NODE=node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
# nginxinc/nginx-unprivileged rather than nginx: UID 101, port 8080, temp paths under /tmp,
# and it runs under the full hardening set with no configuration (docs/ENGINEERING.md §7).
ARG NGINX=nginxinc/nginx-unprivileged:1.31@sha256:4210a3296e7cae7b4f355da48192cd5ea51c88acf9892b95bff967cede85a915

FROM ${NODE} AS build
ENV CI=1
WORKDIR /src
# corepack ships with the image and reads `packageManager` from package.json, so the pnpm
# version is pinned by the same line the developer's machine reads.
COPY hmi/package.json hmi/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY hmi/ ./
# No generated-types step, and deliberately not a no-op one: this screen reads the
# simulator's own snapshot, which has no entry in contracts/ (§10.1 is about the two
# stacks' boundary, and this is inside one stack). simulator/tests/test_hmi.py is what
# holds the payload's names still.
RUN pnpm vite build

FROM ${NGINX} AS runtime
# templates/, not conf.d/: the image's entrypoint runs envsubst over everything here and
# writes the result into conf.d at start, which is what lets the simulator's port be one
# number in the environment rather than three spellings that can drift. conf.d is a tmpfs
# in plant/compose.yml, because the container filesystem is read-only and that write is
# the one thing in this image that has to happen at runtime.
COPY --chown=101:101 hmi/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build --chown=101:101 /src/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
