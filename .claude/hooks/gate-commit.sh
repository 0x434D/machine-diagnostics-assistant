#!/usr/bin/env bash
# PreToolUse(Bash): a commit asserts the quality gates passed.
#
# Instructions alone reach roughly two-thirds compliance; enforcement reaches
# roughly nine-tenths (docs/ENGINEERING.md §1). This is the enforcement half.
# Exit 2 blocks the tool call and returns stderr to the model.
set -uo pipefail

cmd="$(jq -r '.tool_input.command // empty' 2>/dev/null)"
case "$cmd" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

# Degrade silently until the Makefile exists — M1 Task 1 creates it.
root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -f "$root/Makefile" ] || exit 0
grep -qE '^check[[:space:]]*:' "$root/Makefile" || exit 0

if ! out="$(cd "$root" && make check 2>&1)"; then
  {
    echo "BLOCKED: 'make check' failed, so this commit was not made."
    echo "A commit asserts the gates passed. Fix the failures — do not use --no-verify."
    echo
    printf '%s\n' "$out" | tail -40
  } >&2
  exit 2
fi
exit 0
