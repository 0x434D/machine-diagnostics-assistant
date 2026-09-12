# Machine Diagnostics Assistant

Design spec: `docs/superpowers/specs/2026-09-12-machine-diagnostics-assistant-design.md`
Engineering handbook: `docs/ENGINEERING.md` — read before touching tooling, CI or the database.

Keep this file under 200 lines. Instruction files grow ~226% over their lifetime because
appending is cheap and deleting requires verification — when you add a rule here, check whether
an existing one is now dead, and whether a linter could enforce it instead.

The spec is the source of truth. When code and spec disagree, say which one is wrong before
changing either.

## Commands

```
make check    lint + types + tests. The gate. Green before every commit.
make fmt      format in place
make verify   authenticity proofs — stops containers, minutes long, not part of check
```

Single Python test: `cd plant && uv run --package simulator pytest simulator/tests/test_x.py::test_y`
Single C# test: `cd diagnostics/gateway && dotnet test --filter FullyQualifiedName~TestName`

Never run `pytest` or `dotnet` from the repository root — each stack is its own uv workspace
and resolves separately.

## Commits

Commit when a plan step or coherent unit of work is finished. Don't wait to be asked, don't
commit per file, don't leave a session's work in one commit.

Conventional prefixes: `feat(scope):` `fix(scope):` `build:` `test:` `docs:` `refactor:` `chore:`

Write the message for someone reading `git log` in a year with no memory of today: what
changed and **why it was worth changing**. The diff already shows what.

End every message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
Never `--no-verify`. Never push unless asked.

## Do not

These are the failure modes that pass every linter and cost someone an afternoon later.
Each is stated as a prohibition because that is the form that measurably sticks.

- **Do not catch an exception you cannot specifically recover from.** A `try/except` that
  catches, logs and continues turns a loud failure into a quiet wrong answer — and this
  system's entire purpose is to not give quiet wrong answers. Three places have a real
  recovery: the gateway's OPC UA reconnect, its local queue when Postgres is unreachable,
  and the agent's tool loop. Everywhere else, let it propagate.
- Do not swallow cancellation: `OperationCanceledException` and `asyncio.CancelledError`
  mean shutdown. Re-raise.
- **Do not add an interface, factory or base class with one implementation.** The spec names
  the abstractions this project needs. Others are speculation, and speculation ages badly.
- Do not copy a block of logic to a second place. Two copies is where three comes from.
- **Do not add a dependency without justifying it in the commit message** — why the standard
  library or something already here won't do. Models hallucinate package names at a measured
  5–22% rate, so a new import is a thing to be deliberate about.
- **Do not write a test that asserts how the code works.** If a refactor with no behaviour
  change breaks the test, the test was wrong.
- Do not use `Any` or `any` to make a type error go away. Note `mypy --strict` does *not*
  catch explicit `Any` on its own.

## Comments

Comment why, never what. `# increment the counter` is noise. `# the sensor reports lanes
in reverse order on firmware below 2.1` is the reason the line exists and cannot be recovered
from reading the code.

Two things worth knowing, because both are measured: AI-written code carries a materially
higher comment ratio than human code, and roughly one in five of those comments contains a
statement that is not true. Fewer comments, each carrying information that isn't in the code.

- Docstring public interfaces — what it does, what it assumes, what it raises. Not how.
- A short private function with a clear name gets nothing.
- Never restate a type signature in prose. It will drift; the types won't.
- If a comment explains *what* the code does, the fix is a better name or a smaller function.

Comments are **mandatory** in exactly four places: every suppression, every non-obvious
workaround, anywhere the code appears to contradict the spec, and any ugliness that exists for
a measured performance reason.

## Suppressions

Every suppression carries a specific rule code and a reason — enforced by `PGH003`, `RUF100`
and `warn_unused_ignores`, but the *reason* is on you.

```python
value = compute()  # ruff: ignore[F841] retained for the debugger during R1 measurement
```

Suppressing the same rule repeatedly means the rule is wrong for this project. Turn it off in
config with a comment, rather than scattering exceptions through the code.

## Invariants

Violating one of these is quiet, and nothing in the file you are editing will tell you.

- **Only OPC UA crosses between the two stacks.** Exactly two containers join `field-net`: the
  simulator and the edge gateway. No shared database, no shared volume, no second protocol,
  no writes to the plant. This is the one claim the whole architecture exists to support.
- **The diagnostics stack must work with the plant stack shut down.** It answers from history.
- **`SourceTimestamp` is simulated time and is what all analysis uses.** `ServerTimestamp` is
  the wall clock, for diagnostics only. `DateTime.Now` is banned at build time; in Python use
  the injected clock.
- **Two uv workspaces, one per stack.** Never one at the repository root — that would couple
  the stacks at build time. `harness/` is a third, independent project.
- **Each service owns its Postgres schema and holds grants for nothing else.** The analysis
  service reads views and cannot write; the database enforces it, not convention.
- **Ground truth never reaches the diagnostics stack.** If it does, every evaluation number in
  the project is worthless.
- **Every number is configuration** — takt, speeds, thresholds, deadbands, budgets, seeds.

## Subagents

Three concurrent subagents is the ceiling without asking first. Say what you expect it to cost
before going past it.

**Tell every subagent not to spawn its own.** Fan-out compounds: three agents that each spawn
six is twenty-one agents and a budget gone in twenty minutes, which has already happened once
in this repo. Depth beyond one level is almost never worth it, because the top-level agent then
has to wait on and reconcile work it cannot see.

Prefer one well-scoped agent over three overlapping ones, and do the search yourself when you
already know which file holds the answer.

## When unsure

Ask before changing anything in `contracts/`, adding a container to any network, adding a
migration, or adding a dependency.

Say plainly when the spec is ambiguous or wrong rather than picking an interpretation and
moving on. A spec bug found while implementing is worth more than a workaround.
