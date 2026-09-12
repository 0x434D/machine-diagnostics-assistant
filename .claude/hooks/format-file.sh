#!/usr/bin/env bash
# PostToolUse(Edit|Write): format the file that was just written.
#
# Formatting is never a review topic — the formatter decides (docs/ENGINEERING.md §2).
# Best-effort and always silent: a missing tool or an unformattable file is not an error,
# because `make lint` is the actual gate and this is only fast feedback.
set -uo pipefail

f="$(jq -r '.tool_response.filePath // .tool_input.file_path // empty' 2>/dev/null)"
[ -n "$f" ] && [ -f "$f" ] || exit 0

case "$f" in
  *.py)
    if command -v uvx >/dev/null 2>&1; then
      uvx ruff format -- "$f" >/dev/null 2>&1
    elif command -v ruff >/dev/null 2>&1; then
      ruff format -- "$f" >/dev/null 2>&1
    fi
    ;;
  *.cs)
    # Slower than the others: dotnet format loads the whole project. Kept because
    # EnforceCodeStyleInBuild only reports at build time, which is later feedback.
    command -v dotnet >/dev/null 2>&1 && dotnet format --include "$f" >/dev/null 2>&1
    ;;
  *.ts|*.tsx|*.js|*.jsx|*.css|*.json|*.yaml|*.yml)
    if command -v pnpm >/dev/null 2>&1; then
      pnpm exec prettier --write --ignore-unknown "$f" >/dev/null 2>&1
    elif command -v npx >/dev/null 2>&1; then
      npx --no-install prettier --write --ignore-unknown "$f" >/dev/null 2>&1
    fi
    ;;
esac
exit 0
