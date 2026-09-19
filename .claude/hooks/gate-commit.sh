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
# These three mirror .github/workflows/gate.yml's path filters, and
# diagnostics/analysis/tests/test_toolchain.py asserts that they still do — every path CI
# watches for a stack must reach that stack's gate here.
#
# Asserted rather than remembered because this file WAS a second hand-maintained list and had
# already drifted: diagnostics/auth/, diagnostics/knowledge/, scripts/ and measurements/ were
# watched by CI and by nothing here, so a commit touching only the shared validation module
# that decides who gets in ran no Python gate at all. That is the same omission the CI test
# was written for, one list further down.
#
# contracts/ generates types for the Python services, the UI and the C# gateway's own
# contract test, so it triggers all three.
grep -qE '^(plant/|diagnostics/(analysis|agent|auth|issuer|knowledge|mcp)/|diagnostics/(pyproject\.toml|uv\.lock|\.python-version)|contracts/|harness/|knowledge/|measurements/|scripts/|ruff\.toml|mypy\.ini)' <<<"$staged" \
  && targets+=(check-python)
grep -qE '^(diagnostics/gateway/|contracts/|Directory\.(Build|Packages)\.props|BannedSymbols\.txt|NuGet\.config|global\.json)' <<<"$staged" \
  && targets+=(check-dotnet)
grep -qE '^(diagnostics/ui/|plant/hmi/|contracts/|\.nvmrc)' <<<"$staged" \
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
