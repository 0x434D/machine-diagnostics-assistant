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
COPY diagnostics/ui/package.json diagnostics/ui/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY diagnostics/ui/ ./
# The generated types are committed and guarded by the contract drift tests, but the build
# regenerates them anyway: an image built from a stale checkout would otherwise embed types
# that disagree with the contract and say nothing about it.
COPY contracts/ /contracts/
RUN pnpm openapi-typescript /contracts/analysis.openapi.yaml -o src/generated/analysis.ts \
 && pnpm json2ts /contracts/answer.schema.json -o src/generated/answer.ts \
 && pnpm vite build

FROM ${NGINX} AS runtime
COPY --chown=101:101 diagnostics/ui/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=101:101 /src/dist /usr/share/nginx/html
USER 101:101
EXPOSE 8080
