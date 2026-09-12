# Machine Diagnostics Assistant

**Design spec:** `docs/superpowers/specs/2026-09-12-machine-diagnostics-assistant-design.md`

The spec is the source of truth. If the code and the spec disagree, one of them is a bug —
say which, and fix that one. Do not silently follow the code.

---

## Commits

**Commit without being asked.** One commit per completed plan step, or per coherent unit of
work. Never one commit per file, and never a single commit at the end of a session.

- Conventional commits: `feat(scope):`, `fix(scope):`, `build:`, `test:`, `docs:`, `refactor:`, `chore:`
- **A commit means the quality gates passed.** Run `make check` first. If it fails, fix it or
  don't commit.
- Never `--no-verify`.
- Never commit secrets, `.env` files, private keys or certificates.
- End every commit message with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`

Do not push unless asked.

---

## Quality gates

`make check` is the single entry point. It must be green before any commit, and it runs the
same commands CI would.

```
make fmt      format everything, in place
make lint     lint + type-check, no changes
make test     the test suite
make check    lint + test          ← the gate
```

| | Format & lint | Types | Tests |
|---|---|---|---|
| Python | `ruff format`, `ruff check` | `mypy --strict` | `pytest` |
| C# | `dotnet format --verify-no-changes` | `Nullable=enable`, `TreatWarningsAsErrors=true`, `AnalysisMode=All` | `xunit` |
| TypeScript | `biome check` | `tsc --noEmit`, `strict: true` | `vitest` |

Rules:

- **Warnings are errors.** A gate that can be ignored is not a gate.
- **No blanket suppressions.** `# type: ignore`, `# noqa`, `biome-ignore`, `#pragma warning
  disable` each need a specific rule code and a comment saying why. Never file-wide.
- **New code is typed.** No untyped function signatures in Python, no `any` in TypeScript,
  nullable reference types on in C#.
- Formatting is never a review topic — the formatter decides.

---

## Invariants that are expensive to get wrong

These are in the spec, repeated here because violating one is quiet and costly.

- **Only OPC UA crosses between the stacks.** Exactly two containers join `field-net`: the
  simulator and the edge gateway. A third is an architecture violation, not a shortcut.
  No shared database, no shared volume, no second protocol, no writes to the plant.
- **The diagnostics stack must work with the plant stack shut down.** It answers from history.
- **`SourceTimestamp` is simulated time and is what all analysis uses.** `ServerTimestamp` is
  the wall clock and is for diagnostics only. Never mix them.
- **Two uv workspaces, one per stack.** Never one at the repository root — that would couple
  the stacks at build time. `harness/` is a third, independent project.
- **Ground truth never reaches the diagnostics stack.** If it does, every evaluation number
  is worthless.
- **Every number is configuration.** Takt, speeds, thresholds, deadbands, budgets, seeds —
  none of them belong in code as constants.

---

## Layout

```
plant/          simulator, inspection service, plant HMI   — uv workspace
diagnostics/    gateway (C#), analysis, agent, mcp, UI     — uv workspace
knowledge/      SOPs and the defect catalogue, as Markdown
harness/        the evaluation referee — belongs to neither stack
contracts/      OpenAPI — the single source for API, MCP tools and generated TS types
docs/superpowers/specs/    design
docs/superpowers/plans/    implementation plans
```

Toolchain pinning, container conventions and workspace rules: spec §10.7.
