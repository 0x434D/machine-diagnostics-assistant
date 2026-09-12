#!/usr/bin/env bash
# Re-resolve every base image digest without pulling.
#
# Spec §10.7 pins base images by digest, not by tag. This prints the digest each tag
# currently points at so a pin can be refreshed deliberately rather than drifting.
set -euo pipefail
IMAGES=(
  "debian:bookworm-slim"
  "postgres:17-bookworm"
  "node:22-bookworm-slim"
  "ghcr.io/astral-sh/uv:0.11.13"
  "mcr.microsoft.com/dotnet/sdk:10.0"
  "mcr.microsoft.com/dotnet/aspnet:10.0"
)
accept='application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json'
for img in "${IMAGES[@]}"; do
  repo="${img%:*}"; tag="${img##*:}"
  case "$repo" in
    mcr.microsoft.com/*) url="https://mcr.microsoft.com/v2/${repo#mcr.microsoft.com/}/manifests/${tag}"; hdr=() ;;
    ghcr.io/*) path="${repo#ghcr.io/}"
       tok=$(curl -sf "https://ghcr.io/token?scope=repository:${path}:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
       url="https://ghcr.io/v2/${path}/manifests/${tag}"; hdr=(-H "Authorization: Bearer ${tok}") ;;
    */*) path="$repo"; tok=$(curl -sf "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${path}:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
       url="https://registry-1.docker.io/v2/${path}/manifests/${tag}"; hdr=(-H "Authorization: Bearer ${tok}") ;;
    *) path="library/$repo"; tok=$(curl -sf "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${path}:pull" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
       url="https://registry-1.docker.io/v2/${path}/manifests/${tag}"; hdr=(-H "Authorization: Bearer ${tok}") ;;
  esac
  digest=$(curl -sfI "${hdr[@]}" -H "Accept: ${accept}" "$url" | tr -d '\r' | sed -n 's/^[Dd]ocker-[Cc]ontent-[Dd]igest: //p')
  printf '%-46s %s\n' "$img" "${digest:-UNRESOLVED}"
done
