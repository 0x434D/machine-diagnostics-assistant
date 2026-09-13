#!/usr/bin/env bash
# PreToolUse(Bash): a commit asserts the quality gates passed.
#
# Instructions alone reach roughly two-thirds compliance; enforcement reaches roughly
# nine-tenths (docs/ENGINEERING.md §1). This is the enforcement half. Exit 2 blocks the
# tool call and returns stderr to the model.
#
# Runs only the touched stack's gate, mirroring the path filters in .github/workflows/gate.yml
# so there is one description of the gate rather than two. With several worktrees committing
# at once, a full `make check` per commit contends on CPU and on the uv cache; a docs-only
# commit paid for a full Python lint and test run.
set -uo pipefail

cmd="$(jq -r '.tool_input.command // empty' 2>/dev/null)"
case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -f "$root/Makefile" ] || exit 0

staged="$(git -C "$root" diff --cached --name-only)"
[ -n "$staged" ] || exit 0

targets=()
# contracts/ generates types for both the Python services and the UI, so it triggers both.
grep -qE '^(plant/|diagnostics/(analysis|agent|mcp)/|harness/|contracts/|mypy\.ini|ruff\.toml)' <<<"$staged" \
  && targets+=(check-python)
grep -qE '^(diagnostics/gateway/|Directory\.|global\.json|NuGet\.config)' <<<"$staged" \
  && targets+=(check-dotnet)
grep -qE '^(diagnostics/ui/|plant/hmi/|contracts/)' <<<"$staged" \
  && targets+=(check-frontend)
# Makefile or hook changes can invalidate any stack's gate, so fall back to the whole thing.
grep -qE '^(Makefile|\.claude/hooks/)' <<<"$staged" && targets=(check)

[ ${#targets[@]} -gt 0 ] || exit 0

for t in "${targets[@]}"; do
  grep -qE "^${t}[[:space:]]*:" "$root/Makefile" || continue
  if ! out="$(cd "$root" && make "$t" 2>&1)"; then
    {
      echo "BLOCKED: 'make $t' failed, so this commit was not made."
      echo "A commit asserts the gates passed. Fix the failures — do not use --no-verify."
      echo
      printf '%s\n' "$out" | tail -40
    } >&2
    exit 2
  fi
done
exit 0
