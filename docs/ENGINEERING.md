# Engineering handbook

How we work. The design spec describes *what the system is*; this describes *how the code
gets written*. Day-to-day rules live in `CLAUDE.md`; this is the reference behind them.

Tool versions and verdicts were verified September 2026. Anything marked **verify** was not
confirmed against a primary source and should be checked before it is relied on.

---

## 1. Principles

**Enforce, don't exhort.** Every rule below is in one of two buckets: something a machine
checks, or something a person has to remember. Prefer the first.

The gap is measured. In a controlled study across 300 SWE-bench tasks, constraints stated as
natural-language instructions were complied with **67.0%** of the time; the same constraints
enforced executably reached **88.3%**. So roughly one prose rule in three is violated, and the
right instinct on reading a rule in `CLAUDE.md` is *"can this be a lint code instead?"*

One number to distrust if you meet it: a widely-circulated claim of "25–40% prose compliance
versus ~95% with hooks", usually attached to a quote about advisory rules being theatre. Both
trace to a single gist citing an unpublished audit, and the quote does not appear anywhere its
attributed author wrote. It appears to be fabricated and is propagating into search summaries as
fact. The 67.0 / 88.3 figures above are the measured version of the same argument.

The corollary matters just as much: **62% of instruction files contain "Lint Leakage"** —
prose restating what a linter already enforces. It is the most common instruction-file defect
measured, it costs context every session, and it dilutes the rules that genuinely cannot be
automated. When a rule moves into config here, it gets deleted from `CLAUDE.md`.

**A commit asserts the gates passed.** Same principle as the authenticity proofs in spec §1 —
the artifact carries its own proof.

**Boring where it doesn't matter.** Three languages is already enough novelty. Every tool
choice below favours the option with the least configuration surface and the fewest ways to
be subtly wrong.

---

## 2. Quality gates

`make check` is the single entry point, green before every commit, identical to CI.

```
make fmt      format in place
make lint     lint + type-check, no changes
make test     the test suite
make check    lint + test                     ← the gate
make verify   authenticity proofs — slow, container-dependent, not in check
```

`verify` is separate on purpose. The authenticity proofs stop and restart containers; a gate
slow enough to skip is not a gate.

### Python

| Job | Tool | Notes |
|---|---|---|
| Lint + format | **ruff** | 0.16 changed the default rule set from 59 rules to 413. Start from defaults and *subtract*; the old "curate a `select` list" advice is obsolete. |
| Types | **mypy `--strict`** + `disallow_any_explicit` | Add `--num-workers`. mypy 2.0 (May 2026) made parallel checking real, which removes the historical speed objection. |
| Tests | **pytest** | |

**`--strict` does not ban explicit `Any`.** `x: Any` passes silently. Set
`disallow_any_explicit = true`, or the "new code is typed" rule is weaker than it reads.

**Error masking is the rule set that matters most here.** It is the best-evidenced defect in
AI-written code — matched real-repository comparison finds over-representation of CWE-248
(uncaught exception) and CWE-390 (error condition detected, no action), and independent
telemetry measures error-masking constructs up 47%. In a diagnostics system a swallowed error
is a silent wrong answer, which is the precise failure the product exists to prevent. Enable
and keep as errors:

```
BLE001            blind except
S110, S112        try/except/pass, try/except/continue
TRY203            useless try/except re-raise
TRY300            return inside try instead of else
TRY400            logging.error where logging.exception belongs
```

**TODO hygiene is enforced, not asked for:** `TD002` (author), `TD003` (issue link), `TD005`,
`FIX002`. Note Google now discourages bare `TODO(username)` in favour of
`TODO: <link> - <explanation>`; `TD003` is what makes that stick.

**Dead code:** `ARG001`–`ARG005`, `F401`, `F841`. **Not `ERA001`** (commented-out code) — it
has documented false positives on prose that resembles code, so that one stays a review point.

**Suppressions use ruff's own syntax**, which now carries a reason natively:

```python
value = compute()  # ruff: ignore[F841] retained for the debugger during R1 measurement
```

**Why not `ty`.** Astral's checker is still `0.0.x` with no strict-mode preset, so it cannot
enforce "new code is typed". Excellent as an editor language server for instant feedback —
use it there, not as the gate.

**Why not Pyrefly, yet.** Meta's checker went 1.0 in May 2026, is production at Instagram,
has real strictness presets, checks unannotated function bodies by default, and `pyrefly init`
migrates an existing mypy config. It is the credible upgrade path, not ty. Revisit at M6.

**One strategic note.** Astral joined OpenAI in March 2026. The tools are healthy and releases
have continued, but this stack is already heavily Astral-weighted (uv, ruff). Keeping mypy as
the gate means the one non-substitutable quality check does not depend on a single vendor's
post-acquisition priorities. Pyrefly, being Meta's, diversifies rather than concentrates.

**asyncua and `--strict`:** check whether it ships `py.typed`. If not, scope the exception
with `[[tool.mypy.overrides]]` for that module — never loosen globally.

### TypeScript

| Job | Tool | Notes |
|---|---|---|
| Lint | **oxlint** | `create-vite` 9.2 defaults to it. Oxc vendored the React Compiler — 22 of its 24 rules native. Type-aware linting stable since July 2026, 59 of 61 typescript-eslint type-aware rules. |
| Format | **Prettier** | Deliberately *not* oxfmt, which is still 0.x — formatter churn across 0.x releases means diff noise across both frontends. Swap when oxfmt reaches 1.0. |
| Types | **`tsc --noEmit`**, `strict: true` | |
| Tests | **vitest** + React Testing Library | Vitest 5.0 shipped Sept 2026; **`clearMocks` now defaults to `true`** — set it explicitly. |
| E2E | **Playwright** | Use `mcr.microsoft.com/playwright:<version>-noble` so host browser drift can't bite. |

**Not Biome.** It was the 2025 answer and is now the contested one: its React Compiler support
is a single nursery rule with no autofix, its type inference is independently synthesized with
no published accuracy figure, and its 2026 roadmap prioritises neither React nor type-aware
expansion. Next.js chose Biome, Vite chose Oxlint — we are on Vite.

**ESLint 10 + Prettier remains completely defensible** if oxlint's release pace (1.82 in
26 months) is uncomfortable. It costs ~10–20s of CI time for certainty.

**Browser Mode: not yet.** Vitest's browser mode is stable but thinly adopted
(`vitest-browser-react` 983K weekly downloads against RTL's 42.6M), and `vi.spyOn` on imported
objects does not work there because ESM namespaces are sealed in browsers — which breaks a
common React idiom. Stay on jsdom + RTL; add browser mode later for the specific components
where jsdom lies.

**One default that defeats the error-masking rule:** ESLint's `no-empty` treats a block
containing a comment as non-empty, so `catch (e) { /* ignore */ }` — the canonical swallowed
exception — passes out of the box. Configure around it.

**Generated code is excluded from linting.** The OpenAPI-generated types are guarded by the
contract test ("generated TS types must compile"), not by lint rules.

### C#

Target **`net10.0`** — LTS to November 2028. .NET 8 and 9 both reach EOL on 10 November 2026.
.NET 11 is STS; skip it and re-evaluate at .NET 12.

`Directory.Build.props`:

```xml
<Project>
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <Nullable>enable</Nullable>
    <ImplicitUsings>enable</ImplicitUsings>
    <InvariantGlobalization>true</InvariantGlobalization>

    <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
    <WarningsAsErrors>$(WarningsAsErrors);nullable</WarningsAsErrors>
    <WarningsNotAsErrors>$(WarningsNotAsErrors);NU1900;NU1901;NU1902;NU1903;NU1904</WarningsNotAsErrors>
    <EnableNETAnalyzers>true</EnableNETAnalyzers>
    <AnalysisLevel>latest-Recommended</AnalysisLevel>
    <AnalysisModeSecurity>All</AnalysisModeSecurity>
    <AnalysisModeReliability>All</AnalysisModeReliability>
    <EnforceCodeStyleInBuild>true</EnforceCodeStyleInBuild>

    <Deterministic>true</Deterministic>
    <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>
  </PropertyGroup>
</Project>
```

Five things in there are non-obvious and each exists for a reason:

- **No `LangVersion`.** Microsoft explicitly warns against `latest` — its meaning varies by
  machine and can enable features the runtime doesn't support. The TFM already implies C# 14.
- **`AnalysisLevel=latest-Recommended` plus targeted security and reliability**, not
  `AnalysisMode=All`. `All` turns on ~200 rules including "null-check every parameter" and
  "localise your literals" — a day of suppressions for no defect caught. This deliberately
  contradicts the loudest 2026 blog advice; they are optimising for a blog post. Also note:
  when `AnalysisLevel` is compound, it *wins* over `AnalysisMode`, so setting both is
  confusing dead config.
- **`nullable` stays an error even locally.** Deferred nullable warnings are nullable warnings
  nobody fixes, and a null-reference exception is what a gateway running for months cannot
  afford.
- **NuGet audit warnings are carved out of warnings-as-errors.** They honour
  `TreatWarningsAsErrors`, so a CVE published overnight in a transitive dependency breaks an
  unrelated build at an arbitrary time. Re-enable them in a *scheduled* audit job instead.
- **`TreatWarningsAsErrors` covers the C# compiler only** — not MSBuild, NuGet or custom
  tasks. CI must also pass `-warnaserror` on the command line or those slip through silently.

**Analyzers**, as `GlobalPackageReference` in `Directory.Packages.props` (which sets
`PrivateAssets` correctly for you):

| Package | Why |
|---|---|
| **Meziantou.Analyzer** | The best `CancellationToken` propagation rules anywhere (MA0032/40/79/80) — exactly what a `BackgroundService` needs. Apache-2.0, zero open issues. |
| **Microsoft.VisualStudio.Threading.Analyzers** | Catches the sync-over-async and fire-and-forget mistakes that surface at 3am. **Disable VSTHRD001/003/010/011/012** — they assume Visual Studio's main-thread model and are pure noise in a worker service. |
| **Microsoft.CodeAnalysis.BannedApiAnalyzers** | See below. Five minutes, permanent payoff. |
| **Roslynator.Analyzers** | Now maintained in the `dotnet` org, Apache-2.0, no paid tier. |
| **AsyncFixer** | Six async-misuse rules, near-zero configuration. |
| **ErrorProne.NET.CoreAnalyzers** | Beta-only, but the best fit for this workload: leaked `CancellationTokenRegistration` (EPC19), `TaskCompletionSource` without `RunContinuationsAsynchronously` (EPC32), blocking in async (EPC33/35). Opt in deliberately. |

**Not SonarAnalyzer.CSharp.** It left LGPL for the SONAR Source-Available License v1.0, whose
grant excludes *"employing, using, or engaging artificial intelligence technology … to ingest,
interpret, analyze, train on, or interact with the data provided by the Program."* Read
plainly, feeding its diagnostics to an AI assistant falls outside the grant. Not legal advice —
but a reason to pick something else rather than a technicality. **Not StyleCop** (no NuGet
release in 2¾ years). **Not SecurityCodeScan** (dead since 2022).

**`BannedSymbols.txt` turns spec invariants into build errors.** This is the highest-leverage
file in the C# project:

```
T:System.DateTime;use the injected IClock — SourceTimestamp is simulated time (spec §4.2)
M:System.DateTime.get_Now();use the injected IClock
M:System.DateTime.get_UtcNow();use the injected IClock
M:System.Threading.Tasks.Task.Wait();await it
P:System.Threading.Tasks.Task`1.Result;await it
M:System.Threading.Thread.Sleep(System.Int32);use Task.Delay with a CancellationToken
M:Microsoft.EntityFrameworkCore.Infrastructure.DatabaseFacade.EnsureCreated();migrations own the schema
```

The last line matters more than it looks. `EnsureCreated()` is the fastest thing to reach for
in a Compose dev loop and it permanently poisons migrations — Microsoft's own guidance is that
the only way back is to drop the database and recreate it from migrations.

The first three enforce spec §4.2 — that all analysis uses `SourceTimestamp` and never the
wall clock — mechanically, instead of relying on anyone remembering.

**Npgsql 10** has a breaking change that touches the schema: PostgreSQL `date` and `time` now
map to `DateOnly`/`TimeOnly`, not `DateTime`/`TimeSpan`. Register `NpgsqlDataSource` in DI. Its
metrics are now OpenTelemetry-aligned — wire OTel up from the start for a long-running service.

**`dotnet format` is a local convenience; `EnforceCodeStyleInBuild` is the CI gate.** Running
both in CI buys a second, slower way to learn the same thing. Note `dotnet format` was broken
in SDK 10.0.200/10.0.201 — **verify** on whichever SDK is pinned.

### Rules that apply to all three

- **Warnings are errors.** A gate that can be ignored is not a gate.
- **No blanket suppressions.** Specific rule code plus a reason, every time. Never file-wide.
  If the same rule is suppressed repeatedly, turn it off in config with a comment instead.
- **New code is typed.** No untyped signatures in Python, no `any` in TypeScript, nullable
  reference types on in C#.
- Formatting is never a review topic. The formatter decides.

---

## 3. Comments and documentation

The rule, and it is the whole rule: **comment why, never what.**

`# increment the counter` is noise that will rot. `# the sensor reports lanes in reverse order
on firmware below 2.1` is the reason the line exists and cannot be recovered from the code.

Two measured facts behind the emphasis. AI-written code carries a comment ratio of ~19.4%
against ~14.8% for matched human code, with blank-line ratios identical — so it is specifically
comments, not formatting. And when comments were verified against execution, roughly **one in
five contained a demonstrably inaccurate statement.** A wrong comment is worse than none,
because it is believed.

Worth knowing that "why not what" is not the universal consensus it is usually presented as:
Google says why-not-what, but LLVM and the Linux kernel both say *what, not how*. They agree
on the part that matters — don't restate the code, and comment rarely.

**Mandatory** in four places, and only these:

1. Every suppression — rule code and reason.
2. Every non-obvious workaround, naming what it works around.
3. Anywhere the code appears to contradict the spec, naming the section.
4. Any ugliness that exists for a measured performance reason, with the measurement.

**Never:**

- Commented-out code. That is what git is for.
- A comment restating a type signature. The types already say it, and the prose will drift.
- A `TODO` without a name and a date.
- A docstring on a short private function with a clear name.
- A comment explaining *what* the code does — the fix is a better name or a smaller function.

Docstring public interfaces: what it does, what it assumes, what it raises. Not how it works.

---

## 4. Git

**Conventional Commits**, unchanged at spec v1.0.0 since 2019 and still the norm. Prefixes:
`feat(scope):` `fix(scope):` `build:` `test:` `docs:` `refactor:` `chore:`

Commit at completed plan steps or coherent units of work. Not per file. Not once per session.

**Write the message for someone reading `git log` in a year with no memory of today.** What
changed and why it was worth changing — not a restatement of the diff, which the diff already
provides. If a commit needs a paragraph, write the paragraph.

`Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` on every message.

**Never `--no-verify`.** If the hook is slow enough to tempt you, fix the hook.

**Commit linting:** `committed` (crate-ci) rather than commitlint — a single Rust binary, no
Node runtime dragged into a repo that is two-thirds not JavaScript.

**Release tooling: none.** Every release tool automates version bumps and registry publishes.
We publish nothing; the artifacts are container images. **Tag images by git SHA** — immutable,
traceable, no version negotiation. Add `git-cliff` later if human-readable notes are ever
wanted; it reads history that already exists, so it is retroactively adoptable in an afternoon.

---

## 5. Hooks

Three layers, deliberately: fast feedback, a real gate, and an authoritative check.

**Pre-commit hook — seconds only.** Format, lint, secret-scan. Nothing else. Type checking and
tests belong in `make check`. The failure mode is a hook slow enough that people reach for
`--no-verify`.

Use **`prek`** rather than `pre-commit`: a Rust reimplementation reading the *same*
`.pre-commit-config.yaml`, no Python bootstrap needed (which matters for the stacks that
shouldn't need Python at all), and it auto-installs a .NET toolchain for the `dotnet format`
hook. Used by CPython, FastAPI, Godot, Home Assistant and ruff itself. Single maintainer and
pre-1.0 — but because the config format is identical, switching back is a one-line change, so
this is not a lock-in decision.

**Claude Code hooks** in `.claude/settings.json` handle what instructions cannot guarantee:
format after every edit, and run the gate before every commit. See §11.

Know the limits: a `Stop` hook is overridden after 8 consecutive blocks, and `--no-verify`
bypasses git hooks entirely. Hooks raise compliance from roughly two-thirds to roughly
nine-tenths — **CI remains the backstop**, not a formality.

`dotnet format` has no maintained hook repository — use `language: system`.

---

## 6. Dependencies and supply chain

**Renovate**, not Dependabot. Dependabot has supported uv since March 2025 and has closed most
of the gap (grouping, cron schedules, a 3-day cooldown by default since July 2026), but **uv
*workspace* support is undocumented on its side and explicitly documented on Renovate's** — and
this repository has two workspaces. Renovate also maintains Docker *digest* pins and GitHub
Actions SHA pins, which Dependabot does not.

Configuration that keeps it from becoming noise:

- **`minimumReleaseAge: "7 days"`** — the most important line. Supply-chain worms (Shai-Hulud
  in 2025, Mini Shai-Hulud and CHAINDROP in 2026) publish and get yanked within hours. A
  cooldown means you are never the canary. Security advisories bypass it.
- Group all non-major updates into one PR; automerge it, gated on the CI `gate` check.
- **`automergeType: "branch"`** — merges without opening a PR at all, so automerged updates
  produce zero notifications.
- Majors individually, `dependencyDashboardApproval: true`, so they wait as checkboxes in one
  issue.
- `helpers:pinGitHubActionDigests`.

**Verify** Renovate against NuGet Central Package Management with `packages.lock.json` — there
are 2026 reports of it bumping `Directory.Packages.props` without regenerating the per-project
lock files, which breaks locked-mode restore.

**Every new dependency is justified in the commit message.** Why the standard library or an
existing dependency won't do. This is a hard rule, and it exists because adding a dependency is
the cheapest thing an assistant can do and one of the more expensive things to undo.

### Secrets

**GitHub gives us nothing here.** Secret scanning is free and automatic on public repositories
and unavailable at any price for user-owned private ones — it requires an organization-owned
repo on Team or GHEC plus a paid add-on. So there is no platform layer; our own tooling is the
entire defence.

Two layers:

1. **`gitleaks` in the pre-commit hook** — stops the secret before it enters history, the only
   intervention that saves work. Note gitleaks is **feature-frozen** (security patches only);
   its successor **Betterleaks**, from the same authors and MIT, folds in live-credential
   validation. CLI-compatible, so switching is a one-line change. Start on gitleaks, watch
   Betterleaks.
2. **TruffleHog with `--results=verified`** on a weekly full-history CI scan. Verification is
   what turns "47 findings" into "one live key, rotate it now", which matters when there is no
   triage budget.

`.env`, private keys and certificates are in `.gitignore` *and* covered by the scanner. The two
catch different mistakes.

---

## 7. Containers

Beyond what spec §10.7 already requires (digest pinning, uv layering, multi-stage), the things
a naive setup misses, in order of how much they hurt:

**Exec-form `CMD`, always.** `CMD ["thing"]`, never `CMD thing`. Shell form runs the process as
a child of `/bin/sh -c`, which does not forward SIGTERM — so `docker stop` waits out the grace
period and then SIGKILLs. For the gateway that is not cosmetic: a SIGKILL mid-batch exercises
the durable-queue recovery path on every single deploy. Set `stop_grace_period` longer than the
longest in-flight batch. Both the .NET Generic Host and uvicorn handle SIGTERM correctly, so
tini and dumb-init are not needed.

**Non-root with a numeric UID.** `USER 999`, not `USER appuser` — a named user is opaque to
orchestrators that verify `runAsNonRoot`. Use `--chown=` on the COPY rather than a separate
`RUN chown`, which duplicates the layer.

**Base images:** Debian slim (trixie) for Python. `-noble-chiseled` for .NET — and note the
plain `aspnet:10.0` tag still runs as **root**; only the chiseled variants default to non-root
(UID 1654), which contradicts a lot of blog copy. `nginxinc/nginx-unprivileged` for the static
frontends: UID 101, port 8080, temp paths under `/tmp`, and it runs under the full hardening
set with no configuration.

**Chiseled images have no shell**, so a `CMD-SHELL` healthcheck is impossible — including from
the compose side, since those also execute inside the container. The right answer for the
gateway is **a `--healthcheck` flag on its own binary**, which costs nothing and can assert
something HTTP cannot: that the OPC UA session is actually live.

On Alpine and Python: the wheel objection has genuinely weakened (musllinux wheels are now
widely published) and the musl-performance claim is unverified folklore from around 2020. Stay
on slim anyway — 40 MB is not worth a class of "why is this building from source" surprises.

**`.dockerignore` must exclude `.git`, `.venv`, `node_modules`, `bin/`, `obj/`, `**/*.env`,
`__pycache__`.** Getting this wrong costs cache invalidation on every git operation and — worst —
leaks `.env` into images. Copying a host `.venv` into a container produces a broken environment
with host paths baked in.

**Compose:**

- **Compose is on v5.x, not v2.x.** v5.0.0 shipped December 2025 (they skipped 3 and 4 to
  avoid confusion with the dead file-format versions). Two consequences below.
- **No `version:` key.** Obsolete, and it warns on *every* command.
- **`pre_start` init containers (v5.3.0) replace the migration-service pattern.** Ephemeral
  steps that run after `depends_on` is satisfied and before the service starts, each of which
  must exit 0. This is strictly better than a separate `migrate` service plus
  `service_completed_successfully`, and it is what the database section should use.
- **`include:`** is the right tool for two stacks sharing common definitions — each included
  file keeps its own directory and `.env`, unlike `-f` juggling.
- **Compose v5 delegates build to Bake** — the internal builder was removed. You get parallel
  multi-target builds from `docker compose build` for free, so **do not write a separate
  `docker-bake.hcl`**; the compose files already are the bake definition.
- **`depends_on` with conditions** — `service_healthy` for Postgres. Not `restart: true` on
  the gateway; reconnecting is its job.
- **The Postgres healthcheck gotcha, which will bite otherwise.** Naive `pg_isready -U postgres`
  *succeeds during initdb*, while the entrypoint is briefly listening on a unix socket before
  restarting for real. The gateway then connects, gets dropped, and the failure looks like
  something else entirely. Force TCP and give it a start period:
  ```yaml
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB} -h 127.0.0.1"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s
  ```
- **Healthchecks belong in compose, not the Dockerfile** — they are environment-specific and
  belong next to the `depends_on` that consumes them.
- **Postgres on an `internal: true` network, port not published.** Use `docker compose exec` for
  psql. A published 5432 on a laptop is how databases reach the internet. If you must, bind to
  loopback explicitly — `"127.0.0.1:5432:5432"`; the bare `5432:5432` form binds `0.0.0.0`.
- **Do not rely on a host firewall.** Docker writes its own iptables NAT rules that bypass UFW's
  INPUT chain, so a published port is reachable from the LAN even with `ufw deny` in place. The
  defence is not publishing the port, not the firewall rule.
- **Hardening, all of it realistic:** `read_only: true` with `tmpfs: [/tmp]`, `cap_drop: [ALL]`,
  `security_opt: ["no-new-privileges:true"]`, `user: "999:999"`. The gateway's SQLite queue is a
  named volume, which `read_only` does not affect.
- **Log rotation.** The default `json-file` driver has no size limit and will fill a disk given
  a chatty service and time: `max-size: 10m`, `max-file: 3`.
- **Secrets via the top-level `secrets:` block**, mounted at `/run/secrets/<name>` rather than
  put in the environment — keeps the model API key out of `docker inspect` and out of every
  child process's environment.
- `profiles:` keeps the harness and the identity provider out of a default `compose up`.

**OCI labels** — `org.opencontainers.image.source`, `.revision`, `.created`. One line each, and
`.source` is what links a published image back to the repository. Worth knowing that .NET's SDK
container publishing emits the full label set automatically, which a hand-written Dockerfile
almost never does — match it by hand.

**Set `SOURCE_DATE_EPOCH=$(git log -1 --pretty=%ct)`.** Full bit-for-bit reproducibility is not
worth chasing here, but this one line stops the image digest changing when nothing but the clock
did — which is what otherwise makes Compose recreate containers for no reason.

---

## 8. The database

**Verify** the tool licensing below before committing to it — several vendors in this space have
narrowed their free tiers recently. The *architecture* does not depend on the tool choice.

### There are three writers, not one

One Postgres instance, three ownership domains:

| Owner | Writes |
|---|---|
| Edge gateway (C#) | ingest tables — raw events, signals, states, alarms, inspection, genealogy |
| Agent service (Python) | sessions, messages, traces, feedback |
| Identity provider | its own schema, self-migrating, on its own release cadence |

Plus the analysis service (Python), which reads the gateway's tables and writes nothing.

So no single migration tool can own the schema, and "let EF Core own it" or "let Alembic own it"
is wrong on its face.

### Ownership is physical, not conventional

```
ingest.*    owned by the gateway role
agent.*     owned by the agent role
read.*      views over ingest, the analysis service's contract
```

Each service gets its own database role with only the grants it needs. The analysis service
gets `USAGE` on `read` and `SELECT` on its views — no `INSERT`, no `UPDATE`, no reach into
`agent.*`.

That converts "the analysis service must not write to ingest tables" from a code-review
convention into **a permission error at runtime**. Roughly thirty lines of SQL, once, and it is
the highest-value thing in this section.

**One trap that fails silently and late.** `ALTER DEFAULT PRIVILEGES` only affects objects
created by the role you name, and PostgreSQL does not inherit those defaults through role
membership. If the migration runner connects as a deploy role while the grant was written
`FOR ROLE gateway_owner`, every table added afterwards quietly misses its `SELECT` grant — and
nothing surfaces until a reader hits a table added months earlier. Write the default privileges
for whichever role the migration runner *actually connects as*, and assert it in a test.

**The identity provider gets its own database**, not just its own schema. Same Postgres
instance — so spec §10.5's "reusing the existing Postgres" still holds — but its DDL never
interleaves with ours, and the whole ownership domain leaves the problem.

**Readers get views, not tables.** Sharing physical tables across deployment units is the
anti-pattern; sharing an explicitly versioned read contract is just an API that happens to speak
SQL — and for an analytical workload, a better API than HTTP, because the consumer can join and
aggregate. The gateway can restructure underneath as long as the view shape holds, and when it
cannot, changing the view is a deliberate and reviewable act.

### Migrations

**Plain SQL, language-neutral, one migration set per owner.** `dbmate` for the gateway's
`ingest` and `read` schemas; Alembic for the agent's `agent` schema, which is Python-written,
Python-read and Python-modelled — its home ground. Two tools owning disjoint schemas is the
ownership boundary made visible, not fragmentation.

The reason for plain SQL over either ORM's migrations: **the DDL becomes an artifact both
languages can read.** A `.sql` file is legible to the C# author, the Python author, and to
whoever reviews an AI-generated change at 11pm. Neither ORM owns the truth; both are told it.

Two tools were eliminated on verification rather than on taste. **pgroll** implements
expand/contract properly and looked ideal, but it took 10 commits in 2026 against 294 in 2025
(almost all dependabot), and — decisively — it requires **every client to set `search_path` on
every connection**, where forgetting silently bypasses the entire safety mechanism. Six
services and a connection pool make that a matter of when, not if. **Atlas** gates
`migrate lint` behind a paid tier as of v0.38, which is the feature that would have justified
it; squawk does the same job free. Also worth knowing: **Liquibase left open source** at 5.0
for the Functional Source License, which is not OSI-approved.

**The runner-up is `pgschema`** (v1.13.0, past 1.0, Postgres-only, Apache-2.0), which handles
RLS, partitions, grants and views natively — precisely the things generic tools push into raw
SQL. Declarative rather than numbered migrations, which is a bigger leap of faith on a
telemetry database. Note it is a Bytebase product, undisclosed in Bytebase's own tool
comparison.

One quiet argument for dbmate: **sqlc can parse dbmate migrations** (along with atlas, goose,
golang-migrate, sql-migrate and tern) and cannot parse Alembic or EF Core ones. If typed
C#-and-Python codegen from one schema ever becomes attractive, that path stays open.

**Not EF Core**, for two reasons. The physical schema would become an output of the C# object
graph rather than a designed artifact — and with BRIN indexes and composite primary keys, raw
SQL inside `migrationBuilder.Sql(...)` is where it ends up anyway, so it buys ceremony without
convenience. More fundamentally, **EF Core is the wrong data-access layer for this service**:
the ingest path is a batched `COPY` workload where change tracking is pure overhead. Use Dapper
or raw Npgsql with `NpgsqlBinaryImporter`. Once EF is not the data layer, using it solely for
migrations is the tail wagging the dog.

**Mechanics:**

- **Migrations run as a `pre_start` step** on the owning service (Compose v5.3+), never from
  the application's own entrypoint. Startup migrations race across replicas and put migration
  tooling into an image that does not need it. Only the owning stack may run them: the Python
  stack must *assert* the schema version and refuse to boot on a mismatch, not apply it.
- **Advisory locks break under PgBouncer in transaction-pooling mode** — they are
  session-scoped, so the lock is silently lost. Migrate on a direct connection. (Correcting a
  common belief: EF Core 9+ *does* take a migration lock automatically — but not for SQL
  scripts.)
- **`SET lock_timeout` at the top of every migration.** An `ACCESS EXCLUSIVE` acquisition that
  queues behind a long reader blocks every subsequent query on that table. Three seconds turns
  an outage into a failed migration you retry.
- **Expand/contract** for anything destructive: add column → backfill → dual-write → switch
  reads → drop. Single writer makes this easy; do it anyway, because the readers deploy
  separately.
- **`squawk`** as a migration linter — catches migrations that take dangerous locks. Cheap,
  language-neutral, good.
- **Schema drift detection in CI.** Start an empty Postgres, apply all migrations,
  `pg_dump --schema-only`, diff against a committed `schema.sql`, fail on difference. That one
  job eliminates the entire class of "the database does not match what anyone believes", and
  the committed dump doubles as the artifact humans and assistants read to learn the schema.
- **The Python readers do not mirror the schema in an ORM.** The analysis service is read-only
  over a fixed endpoint list. Use raw SQL with Pydantic row models, and add a CI test that
  executes every query against a freshly migrated database. That test *is* the contract, it
  catches the same breakages, and it avoids maintaining a second model of tables nobody writes
  from Python.

---

## 9. CI/CD

**CI contains no logic.** Every step is `make <target>` and nothing else. The moment a workflow
grows an inline `run:` that does real work, CI and local have diverged and the spec's "identical
to what CI runs" is false.

**Change detection with `dorny/paths-filter`** — not `tj-actions/changed-files`, which was
compromised in a March 2025 supply-chain attack that dumped CI secrets into build logs across
tens of thousands of repositories. Remediated, but there is no reason to take the risk.

Filter boundaries follow the repository: `plant/`, `diagnostics/`, `harness/`, `contracts/`,
`knowledge/`. **A change to `contracts/` must trigger both the Python and the TypeScript jobs**,
since it generates types for both.

**The aggregating gate job — this one is not obvious and will otherwise waste an afternoon.** A
required status check that is skipped by a path filter never reports, so the pull request blocks
forever. The fix:

```yaml
gate:
  needs: [python, dotnet, frontend]
  if: always()
  runs-on: ubuntu-latest
  steps:
    - if: contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')
      run: exit 1
```

Make **only `gate`** a required check.

**Caching:** `astral-sh/setup-uv` with `enable-cache` and `cache-dependency-glob` pointing at
both `uv.lock` files; `pnpm/action-setup` before `actions/setup-node` with `cache: pnpm` (order
matters); `actions/setup-dotnet`'s built-in NuGet caching, which works because the lock files
are committed.

**Security baseline, non-negotiable:** pin every third-party action to a full commit SHA with a
version comment; `permissions: contents: read` at workflow level; `persist-credentials: false`
on checkout. Add **`zizmor`** (Actions security linter — catches template injection, over-broad
permissions, unpinned actions) and `actionlint`.

**Supply chain:** Trivy on every built image, failing on HIGH and CRITICAL with fixes available.
BuildKit SBOM and provenance attestations (`--sbom=true --provenance=true`) — but note BuildKit
**only scans the final stage**, so a multi-stage build's SBOM omits everything installed in the
builder. Widen it with `BUILDKIT_SBOM_SCAN_CONTEXT=true` and `BUILDKIT_SBOM_SCAN_STAGE=true`.

Signed SLSA provenance is now roughly three lines of YAML with keyless signing, which moved it
from advanced to no-excuse — use **`actions/attest`** rather than `actions/attest-build-provenance`,
which is now just a wrapper and points new implementations at the former. GitHub's artifact
attestations get you SLSA Build L2 on their own; L3 additionally requires reusable workflows.
Do not start from `slsa-framework/slsa-github-generator`, which is no longer actively maintained.
And `uv export --format cyclonedx` gives a dependency SBOM straight from the lockfile.

**One reason this is not optional, and the date has passed.** The EU Cyber Resilience Act's
vulnerability-reporting obligations **applied from 11 September 2026**; the main obligations
follow on 11 December 2027. What it actually requires on SBOM is narrower than the hype: a
machine-readable bill of materials covering *at least top-level dependencies*, in no mandated
format, disclosed to authorities rather than published. Scope turns on placing a product on the
EU market commercially. **Verify** whether this product falls in scope — that is a legal
question, not a tooling one, but it is why SBOM moved from advanced to baseline.

**No build orchestrator.** Nx, Turborepo, Bazel, Moon, Pants, Dagger and Earthly are all net
negatives at eight services and one developer — and Earthly is sunsetting while Dagger has
pivoted to AI agents. Two independent stacks with independent lockfiles plus path-filtered jobs
already provide what these tools sell. Revisit if `make check` ever exceeds a few minutes
locally, or if a second developer arrives. Neither is near.

### What the pipeline actually is, and what the plan buys

The pipeline is `.github/workflows/gate.yml` plus `weekly.yml`, and both contain only `make`
calls. **`make ci` runs the whole of it locally** — no remote required, which is the point of
the no-logic rule rather than a side effect of it.

**The plan decides more than the price.** On a free personal account, public and private are
two different products, and three things in this section only exist on one side of that line:

| | Public repo, free | Private repo, free |
|---|---|---|
| Required status checks / rulesets | yes | **no** — the `gate` job cannot be made required |
| Actions minutes | unlimited | 2,000/month |
| Artifact attestations (`actions/attest`) | yes | no (Team/Enterprise) |
| GitHub secret scanning | yes, automatic | no, at any price (§6) |

Without required checks the aggregating `gate` job still reports, but nothing stops a merge on
red — it becomes a signal rather than a gate, and the commit hook is left carrying the
enforcement alone. That is the trade, and it is the reason attestations and `actions/attest`
are not wired up yet: on a private free repo they cannot work, and there is no registry to
push to either. Revisit both together when one arrives.

**Four corrections to the paragraphs above, each found by running the thing:**

- `uv export --format cyclonedx` is rejected — the accepted value is **`cyclonedx1.5`**.
- **Trivy cannot read an OCI *tar***, only an OCI *directory* or a Docker-format tar. Export
  with `type=oci,tar=false`; the failure otherwise is a misleading "manifest.json not found".
- The default `docker` buildx driver silently drops attestations, so the image targets create
  their own pinned `docker-container` builder. Doing that in the Makefile rather than via
  `docker/setup-buildx-action` is what keeps the runner from being able to build images in a
  way a developer cannot.
- **BuildKit pulls `docker/buildkit-syft-scanner` at build time to generate the SBOM.** Left at
  its default tag it is an unpinned third-party image with read access to every layer it scans
  — the one hole all the other pinning would leave open. Pin it via `--sbom=generator=<image>`.

And one deliberate deviation: `dotnet format --verify-no-changes` is kept in the CI path even
though §2 calls it redundant next to `EnforceCodeStyleInBuild`. Spec §10.8's "identical to what
CI runs" is the stronger invariant — a CI target that is a subset of the local one is exactly
the divergence this section exists to prevent, and the duplication costs seconds.

---

## 10. Error handling and logging

**Let it fail.** A `try/except` that catches, logs and continues turns a loud failure into a
quiet wrong answer. Catch a *specific* exception at a boundary where there is a *specific*
recovery. Everywhere else, let it propagate.

The three boundaries in this system that genuinely have a recovery, and their behaviour is
specified: the gateway's OPC UA reconnect, the gateway's local queue when Postgres is
unreachable, and the agent's tool loop, which returns tool errors to the model as tool results.
A catch anywhere else needs the mandatory comment from §3.

**Never swallow a cancellation.** `OperationCanceledException` and `asyncio.CancelledError` mean
shutdown; re-raise them.

**Logging:** structured JSON, a correlation id threading a question through agent → analysis →
database. `ERROR` means someone must act; `WARNING` means something recovered but shouldn't
recur. If everything is a warning, nothing is. Log an event **once**, at the level that has the
context to describe it — not at every frame on the way up. Never log secrets, tokens, or the
contents of `.env`.

---

## 11. Claude Code configuration

`CLAUDE.md` is advisory. Hooks are deterministic. The dividing line: **if it would be caught in
code review, it belongs in `CLAUDE.md`; if it would be caught by CI, it belongs in a hook.**

Configured in `.claude/settings.json`:

- **`PostToolUse` on `Edit`/`Write`** — format the file that was just written. Formatting should
  never be something anyone thinks about.
- **`PreToolUse` on `git commit`** — run `make check` and block the commit on failure. This is
  what makes "a commit asserts the gates passed" true rather than aspirational.

`CLAUDE.md` stays under 200 lines: adherence measurably degrades past that, and anything
derivable from reading the code is context spent every session for nothing. Note that `@imports`
load eagerly and therefore save no context — this file is referenced by path, not imported, so
it costs nothing until it is needed.

Subdirectory `CLAUDE.md` files load lazily when files in that directory are read, which is the
right home for per-language conventions once they grow.

---

## 12. Review

What to look at first in a diff, in this order:

1. **Does it match the spec?** If it deviates, is the deviation named and argued, or silent?
2. **What was deleted?** Deletions are where behaviour disappears unremarked.
3. **New dependencies** — is the justification in the commit message?
4. **New suppressions** — rule code and reason present?
5. **New `try/except`** — specific exception, specific recovery, or a swallowed failure?
6. **Tests** — do they assert behaviour, or implementation? Would a pure refactor break them?
7. **Comments** — do they say *why*, or restate the code? Is what they claim actually true?

Worth knowing what no tool catches: a test with **zero assertions** is not detected by any rule
in ruff's index or the pytest plugin set — it is about thirty lines of custom AST walk, and worth
writing given how confidently a passing empty test reads. Nor is semantic duplication, over-
mocking, comment accuracy, or whether the author understands their own code.

Two of these can be partly mechanised and should be: **mutation testing on the diff** (not the
whole repo) is the only real check on whether tests assert behaviour, and
`actions/dependency-review-action` gates the lockfile diff on the PR, which is the right
control point given the measured rate of hallucinated package names.

The most common way a change is wrong here is not that it fails; it is that it works while
quietly violating one of the invariants in `CLAUDE.md`, because nothing in the file being edited
mentions them.
