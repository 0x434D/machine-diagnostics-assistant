#!/usr/bin/env bash
# Re-resolve every third-party GitHub Action to the commit SHA its tag points at.
#
# Handbook §9 requires every action pinned to a full commit SHA with a version comment: a
# mutable tag is an arbitrary third party's write access to our CI. This prints the pin line
# for each, so refreshing one is a deliberate edit rather than drift. The companion to
# scripts/pin-images.sh, which does the same for base image digests.
#
# Renovate keeps these current once it is installed (`helpers:pinGitHubActionDigests`); this
# script is what you run before that, and when you want to check Renovate's work by hand.
set -euo pipefail

ACTIONS=(
  "actions/checkout@v7.0.1"
  "dorny/paths-filter@v4.0.3"
  "astral-sh/setup-uv@v10.1.0"
  "actions/setup-dotnet@v6.0.0"
  "actions/setup-node@v7.0.0"
  "pnpm/action-setup@v6.1.0"
  "actions/upload-artifact@v7.0.1"
)

# GitHub's commits endpoint dereferences annotated tags for us, so this is one call per action
# rather than the ref-then-deref dance. Unauthenticated is 60 calls/hour; pass GITHUB_TOKEN if
# you hit it.
auth=()
[ -n "${GITHUB_TOKEN:-}" ] && auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}")

for spec in "${ACTIONS[@]}"; do
  repo="${spec%@*}"; tag="${spec##*@}"
  sha=$(curl -sf "${auth[@]}" -H 'Accept: application/vnd.github+json' \
          "https://api.github.com/repos/${repo}/commits/${tag}" \
        | sed -n 's/^  "sha": "\([0-9a-f]\{40\}\)".*/\1/p' | head -1)
  printf '%-30s %s # %s\n' "$repo" "${sha:-UNRESOLVED}" "$tag"
done
