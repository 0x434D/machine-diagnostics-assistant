# M3 — The analysis is real

**Goal:** The service computes what follows necessarily from the data — a line stop found from output rather than from state, a propagation chain returned as a visible derivation rather than a verdict, patterns that carry a significance verdict and may correctly return nothing, coverage that says where data is missing, and the traceability queries that answer "which parts are affected". No model is involved anywhere in this milestone, which is why its correctness is checkable.

**Architecture:** Every M3 endpoint reads through `read.*` views owned by no service and granted `SELECT` only to the analysis role — so "the analysis cannot write" becomes a permission error rather than a convention. Propagation is a graph walk over state episodes and buffer levels, pure and unit-testable, with the HTTP layer a thin shell over it. Statistics are hand-rolled in stdlib against the noise floor M2c produced.

**Spec:** `docs/superpowers/specs/2026-09-12-machine-diagnostics-assistant-design.md` §5.3, §5.4, §5.5, §8.4.

---

## Global Constraints

Every task's requirements implicitly include this section.

**The boundary (§2.1, §2.2)**
- The diagnostics stack must answer with the plant stack shut down. Every M3 endpoint reads Postgres and nothing else.
- **Ground truth never reaches the diagnostics stack.** `plant/`'s gt volume is mounted by no diagnostics container and no M3 code may read it, directly or by path. If it did, every number in M7 would be worthless.
- M2c's scenarios declare twelve *consequences* and deliberately name no cause. **Inferring the cause is M3's job.** Do not copy an expected cause out of `scenarios.py` into a test — that would make M7's scoring circular.

**Time (§3.2, §4.2)**
- All timestamps stored UTC; all shift logic `Europe/Berlin`. Shifts: early 06–14, late 14–22, night 22–06. "Last night" is the most recent **completed** night shift.
- **All analysis uses `SourceTimestamp`**, never `ServerTimestamp`, never arrival order. Ordering is by `source_ts` on read.
- Windows are half-open `[from, to)`, matching what M1/M2 already built.

**Data the analysis must not trust blindly**
- `inspection_results.defect_class` (scalar) and `.positions` are **dead columns** — populated for pre-M2b rows only, and never. Use `defect_classes[]`/`confidences[]`.
- `part_station_events` is empty **by design**; the plant publishes no station entry/exit event. Station history is `assemblies.created_at` + `part_process_values` + `part_process_curves` + `inspection_results` + `part_dispositions`.
- `component_lots.depleted_at` is never written; derive it if needed.
- Gaps are first-class: a window containing an `ingest_gaps` row is not a quiet window.

**§10.3 — every number is configuration**: the micro-stop threshold, the lead-in, minimum sample sizes, significance alpha, cache TTL, aggregation bucket sizes.

**Quality gates**
- `make check` green before every commit; the hook runs the touched stack's gate. Never `--no-verify`.
- `mypy --strict` with `disallow_any_explicit`. No blanket suppressions — rule code plus a local reason.
- **Every new endpoint requires `make contract` immediately afterwards**, or `test_contract.py` fails. That test is the enforced gate and a hard dependency of every task here.
- **Do not catch an exception you cannot specifically recover from.** The three places with a real recovery are named in CLAUDE.md and none of them is in this milestone.
- Conventional prefixes, exactly: `feat` `fix` `build` `test` `docs` `refactor` `chore`.

**Dependencies**: there is no statistics library anywhere in this repo and every significance number so far was hand-rolled in stdlib. Task 8 either continues that or justifies a dependency in its commit message.

---

## The line this milestone must not cross

- **No model, no agent, no knowledge base.** M3 computes; M4 interprets. If a question needs judgement, it is not M3's.
- **No `sessions`/`messages`/`traces`/`feedback` tables** — those are the agent's, in M4.
- **No new citation kinds** in `contracts/answer.schema.json`. M3 builds the endpoints; M4 widens the citation vocabulary that reaches them.
- **No UI.** M6.
- **No reading of `knowledge/`.** `GET /knowledge/{id}` is listed in §5.3 but serves the knowledge base, which is M4's; it lands there.

---

## What M2 left that this builds on

- Four stations with PackML state changes carrying `reason` and `reason_buffer_id`, three buffers with levels, alarms with a raised/acked/cleared lifecycle.
- Per-part process values and force–distance curves, keyed on `assembly_serial` at the instant of production — never reconstructed by a time join.
- Serialised components with lots, assemblies with genealogy, carriers.
- A noise floor with a per-carrier baseline quality drawn independently of any injected fault. **This is why a significance test is needed rather than a `GROUP BY`.**
- `state_changes_settled` — the one existing view, filtering rows where `to_state IS NOT NULL`.

---

## Task 1: `read.*` views, the analysis role, and the grant that makes the claim true

CLAUDE.md states as an invariant: *"Each service owns its Postgres schema and holds grants for nothing else. The analysis service reads views and cannot write; the database enforces it, not convention."* Today every table is flat in `public`, the gateway and the analysis service share one superuser, and `analysis/db.py`'s own docstring asserts an enforcement that does not exist. That docstring is the most load-bearing untrue sentence in the repository.

M3 is where this gets fixed, because M3 is where the analysis service stops being two endpoints and becomes the contract.

**Files:** `diagnostics/gateway/Gateway/Migrations/005_m3_read_layer.sql`; `diagnostics/analysis/src/analysis/db.py`; `diagnostics/compose.yml`; `diagnostics/analysis/tests/test_read_layer.py`

- [ ] **Step 1: Write the failing test.** Assert that the analysis role can `SELECT` from every `read.*` view it needs; that `INSERT`/`UPDATE`/`DELETE` on any `ingest` table raises `InsufficientPrivilege`; and that the analysis role cannot read a table it has no view for. Use the existing `testcontainers` conftest.
- [ ] **Step 2: Write migration 005.** Create schema `ingest`, move every existing table into it, create schema `read` with one view per consumed table, create role `analysis` with `USAGE` on `read` and `SELECT` on its views and nothing else. `ALTER DEFAULT PRIVILEGES` must name **the role the migration runner actually connects as** — it is not inherited through role membership, and getting this wrong means every table added later silently misses its grant.
- [ ] **Step 3: Point the analysis service at the `analysis` role** via a separate `ANALYSIS_DATABASE_URL` in compose; the gateway keeps its own.
- [ ] **Step 4: Correct `db.py`'s docstring** so it describes what is now true.
- [ ] **Step 5: `make check`, `make contract` (no API change expected), commit.**

## Task 2: `/time/resolve` — the calendar, in code

§6.1 step 2 splits deliberately: the model reads "last night" out of a sentence, code computes what it means. Date arithmetic across shift boundaries and DST is what models are unreliable at.

**Files:** `diagnostics/analysis/src/analysis/time_windows.py`, `routes_time.py`, tests

- [ ] **Step 1: Failing tests.** `last night`, `this shift`, `last shift`, `today`, `yesterday`, `last hour`, `this week`, and an unparseable expression. Assert the autumn DST night resolves to nine hours and the spring one to seven. Assert "last night" at 05:00 resolves to the shift that ended *yesterday* at 06:00 — the same rule that made M1's history depth 33 h.
- [ ] **Step 2: Implement** `resolve(expression, now) -> Window | None`, pure, no I/O. Reuse the plant's shift constants only by value, not by import — the stacks share no code.
- [ ] **Step 3: `GET /time/resolve?expression=`** returning the window, the shift name, and whether it is closed. Unresolvable is a 422 with the list of understood expressions, never a guess.
- [ ] **Step 4: `make contract`, commit.**

## Task 3: `/coverage` — where the data is not

M2b left this as an explicit TODO: `/coverage` is folded into `/inspection/stats` and splitting it out is M3's.

**Files:** `routes_coverage.py`, `coverage.py`, tests

- [ ] **Step 1: Failing tests.** A window with no gaps; a window wholly inside a gap; a gap overlapping one edge; several gaps. Assert the response distinguishes *"no data because the line was quiet"* from *"no data because ingest was down"* — that distinction is the entire reason the endpoint exists.
- [ ] **Step 2: Implement** coverage as observed span versus requested span, with the gap rows that overlap and a computed covered fraction.
- [ ] **Step 3: Keep `/inspection/stats.coverage`** working by calling the same function — one implementation, two exposures.
- [ ] **Step 4: `make contract`, commit.**

## Task 4: Stop detection — defined by output, not by state

§5.4: a line stop is *no part leaving S4 for longer than the micro-stop threshold of 60 s*. Shorter interruptions are micro-stops, counted but not stop events — and a rising micro-stop count is its own diagnostic signal.

**Files:** `stops.py`, tests

- [ ] **Step 1: Failing tests.** A clean run produces no stops. A 45 s interruption is a micro-stop, not a stop. A 90 s interruption is one stop with the right boundaries. Two interruptions 30 s apart are two events, not one. A stop open at the window edge is reported as open, not silently closed.
- [ ] **Step 2: Implement** over `part_dispositions` (S4's output), configurable threshold, returning episodes with start, end, duration and a micro-stop count for the window.
- [ ] **Step 3: Assert output-defined, not state-defined** — a test where S2 is `Aborted` but S4 keeps emitting from the buffer must produce **no** stop until the buffer drains. This is the test that proves the definition was implemented rather than the intuition.

## Task 5: Propagation — a derivation, not a verdict

§5.4's four steps, returned as a structured chain the agent can later contradict.

**Files:** `propagation.py`, tests

- [ ] **Step 1: Failing tests, one per category.** `external_upstream` (chain ends at S1 starved), `external_downstream` (ends at S4 blocked), `internal` (ends at a station in `Held`/`Aborted`), `ambiguous` (two independent cause candidates).
- [ ] **Step 2: Implement** the walk: pull every station's state timeline across the window plus a configurable lead-in; classify each non-`Execute` episode by PackML semantics — `Suspended` is a consequence **by definition**, `Held` and `Aborted` are cause candidates; follow each consequence's `reason_buffer_id` to the buffer, find when it ran empty or full from `buffer_levels`, find which station's episode explains that, repeat backwards.
- [ ] **Step 3: Return the chain as data** — each link carrying the station, the episode, the buffer that connects it to the next link, and the timestamps. The category is derived from where the chain terminates, never assigned directly.
- [ ] **Step 4: The circularity guard.** A test asserting the root is **not** simply "the first station to raise an alarm" — build a fixture where the first alarm is downstream of the true root and assert the chain still terminates upstream. Two of M2c's eight scenarios raise no alarm at all; `004_m2c.sql`'s own header warns about exactly this.

## Task 6: `/stops` and `/stops/{id}`

**Files:** `routes_stops.py`, models, tests

- [ ] **Step 1:** `GET /stops?from&to` — the list with durations and a provisional category.
- [ ] **Step 2:** `GET /stops/{id}` — the full state timeline across the window plus the propagation derivation, shaped so a UI can draw it as a Gantt with the chain overlaid.
- [ ] **Step 3:** Stop ids must be **stable and resolvable**, because §6.5 verifies every cited id against the database. A synthetic id derived from the stop's start timestamp is fine; a row-number is not.
- [ ] **Step 4: `make contract`, commit.**

## Task 7: `/alarms`, `/signals/trend`, `/line/status`

**Files:** `routes_alarms.py`, `routes_signals.py`, `routes_line.py`, tests

- [ ] **Step 1:** `GET /alarms?from&to&station` over the lifecycle — raised, acknowledged, cleared, and still-active.
- [ ] **Step 2:** `GET /signals/trend?station&signal&from&to&agg` with `agg` in `raw|minute|hour`, aggregating in SQL not in Python.
- [ ] **Step 3:** `GET /line/status` — what is happening right now: each station's current state and reason, buffer levels, active alarms, the last part out, and **how stale the data is**. With the plant down this must answer honestly rather than appear live.
- [ ] **Step 4: `make contract`, commit.**

## Task 8: Significance — and the answer "nothing"

§5.5: for any dimension the service returns observed share, expected share, sample size, effect size and a significance verdict, with a minimum-sample gate. *"Carrier 7 is 4 % above average, n=38, not significant"* is a valid and important answer.

**Files:** `statistics.py`, `patterns.py`, `routes_patterns.py`, tests

- [ ] **Step 1: Choose the test and justify it in the commit message.** §15 deferred this to M3 because it needs the noise floor's real distribution, which M2c now produces. Two-proportion z is adequate above the sample gate and is stdlib arithmetic; binomial exact is better at small n and is where the gate sits anyway. Decide, state why, and write the choice into the module docstring.
- [ ] **Step 2: Failing tests that matter.** A dimension with a real injected effect is significant. **A dimension with only the noise floor's carrier-quality variation is not** — this is the test that distinguishes M3 from a `GROUP BY`, and it must use the same log-normal spread M2c's `noise.py` produces. A dimension below the minimum-sample gate returns *not enough data*, distinct from *not significant*.
- [ ] **Step 3: Implement** `/inspection/patterns?from&to` over carrier, lane, defect class and time bucket, and add the missing `group_by` to `/inspection/stats`.
- [ ] **Step 4: `make contract`, commit.**

## Task 9: Traceability and containment

§5.3: *"`/parts/affected` is the query traceability exists for."*

**Files:** `routes_traceability.py`, `containment.py`, tests

- [ ] **Step 1:** `GET /parts/affected?from&to&criteria` — the containment scope. Criteria at minimum: passed a station in a window, rode a carrier, contain a lot, were inspected with a defect class. The response separates **rejected** from **shipped and needing checking** — that split is the answer a plant needs at three in the morning.
- [ ] **Step 2:** `GET /lots/{lot_code}/parts`, `GET /components/{serial}/assembly`, `GET /carriers/{id}/parts`.
- [ ] **Step 3: The no-time-join guard.** Every one of these reads the per-part record directly. A test must fail if any implementation reconstructs association by joining the time series — the fixtures already carry deliberate decoy rows with disjoint value ranges for exactly this.
- [ ] **Step 4: `make contract`, commit.**

## Task 10: Closed-window caching

§5.3: results over a closed window can never change, so they cache indefinitely with no staleness risk; only the currently-running window needs a short TTL.

**Files:** `cache.py`, wiring, tests

- [ ] **Step 1: Failing tests.** A closed window is computed once and served from cache. A window whose end is in the future is not cached beyond the TTL. A gap arriving late for a closed window invalidates it — otherwise backfill would be invisible to a cached answer, which is worse than no cache.
- [ ] **Step 2: Implement** in-process, keyed on the endpoint and its normalised parameters, with size and TTL as configuration.

## Task 11: What M3 proves

`measurements/authenticity/README.md` row 1.5 says the analysis proof waits on M3. This task pays it.

**Files:** `diagnostics/analysis/tests/test_analysis_proof.py`, `measurements/authenticity/README.md`

- [ ] **Step 1:** For each of M2c's eight scenarios, seed the database from the *observable consequences* the scenario declares — not from its injected fault — and assert the computed root station and category. Scenario 1 must resolve `external_upstream`, 2 `external_downstream`, 3 `internal` at S2.
- [ ] **Step 2: Scenario 7 is the one that matters.** Rising `gap` with a *stable* joining force must not resolve to a drift at S2. M3 cannot yet name the lot as the cause — that is interpretation, and M4's — but it must not assert the wrong mechanical cause, and `/inspection/patterns` must show the lot dimension as significant while the force trend is not.
- [ ] **Step 3: Mark these `authenticity`** so they run under `make verify`, and update the README's table to move row 1.5 from "not provable yet" to proved, with the measurement.
- [ ] **Step 4:** Record in the report which of §1's nine proofs M3 closes and which remain.

---

## Spec ambiguities this plan resolves

- **The `read.*` layer never existed**, though CLAUDE.md states it as an invariant and `db.py` claims it. Task 1 builds it rather than deferring again.
- **`GET /knowledge/{id}`** is in §5.3's list but serves the knowledge base — deferred to M4 with the endpoint list otherwise complete.
- **The significance test** was deferred to M3 by §15 and is chosen in Task 8 with its reasoning recorded.
- **Stop ids** are unspecified in §5.3 but §6.5 requires every cited id to resolve; Task 6 makes them derived and stable rather than positional.
