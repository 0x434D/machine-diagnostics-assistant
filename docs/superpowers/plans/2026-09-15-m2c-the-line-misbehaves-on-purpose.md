# M2c — The Line Misbehaves On Purpose — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Six fault types, eight declarative scenarios, a permanent noise floor, a ground-truth log that records what was injected and what should follow, and alarms — so the plant misbehaves on purpose and says exactly how.

**Architecture:** A fault is a **modifier on normal behaviour**, not a special-case branch — it changes a number where that number is computed, so nothing downstream knows a fault exists. Scenarios are declarative `(offset, fault, params)` lists that fire from a script during catch-up or by hand from the HMI. Every injection writes to ground truth on the gt volume, which the diagnostics stack must never see.

**Tech Stack:** Python 3.13 · asyncua 2.0.1 · .NET 10 · PostgreSQL 17 · React 19

**Spec:** `docs/superpowers/specs/2026-09-13-m2-the-plant-is-real-design.md` (D8, D13) implementing §3.5, §3.6 and §3.7 of `docs/superpowers/specs/2026-09-12-machine-diagnostics-assistant-design.md`. **Read both.**

---

## Global Constraints

- **Ground truth never reaches the diagnostics stack.** If it does, every evaluation number in the project is worthless. It lives on the gt volume; no diagnostics container mounts it.
- **Only OPC UA crosses between the stacks.** Exactly two containers on `field-net`.
- **`SourceTimestamp` is simulated time.** Faults are injected at simulated instants, recorded at simulated instants.
- Two uv workspaces. Never `pytest`/`dotnet` from the repository root.
- **Every number is configuration** — and in this milestone that is load-bearing, because the ratio between noise and injected fault is what decides whether a scenario is findable.
- `mypy --strict`, `disallow_any_explicit`, no `Any`. Every suppression carries a rule code **and** a reason.
- Do not catch an exception you cannot specifically recover from. Do not swallow cancellation.
- **Do not write a test that asserts how the code works.** This milestone has a specific hazard: a test that asserts a fault was *injected* rather than that its *consequence appeared* is testing the injector, not the plant.
- `make check` green before every commit. Commits: conventional prefix, WHY in the body, ending `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Never `--no-verify`.

---

## The line this milestone must not cross

**M2c proves the faults happened and were recorded. It does not diagnose anything.**

Whether the analysis reaches the right cause is M3; scoring it is M7. Every temptation to add "and the analysis then finds it" belongs to a later milestone. Without this line M2c quietly absorbs M3, and the ground-truth log — the thing that makes every later evaluation number meaningful — gets written by the same reasoning that will later be graded against it.

---

## What M2a and M2b left, that this builds on

- A discrete-event queue with four stations, three buffers, eighteen carriers. `Line.hold(code, reason)` / `unhold(code)` are **async and publish** their PackML transitions.
- `Settings.station_takt_seconds` makes S3 the bottleneck so its upstream buffers fill; `carrier_count` is 18 because 12 made S1 invent an upstream shortage in 29% of its suspended cycles.
- Identity: component serials in lots (`L-nnnn`, `SUP-nn`), assembly serials, genealogy, a press curve whose **contact point** is the component knob and whose **clamp force** is the press knob — separable in both directions, which is what makes scenarios 3 and 7 distinguishable.
- `SimulatedClassifier` owns false-accept and false-reject rates (D7) and emits six independent per-class scores plus a scalar verdict confidence.
- 25 historised streams, five event types, ingest and backfill for all of them.
- The plant's `_next_takt` still carries M1's resample guard. **D13 deletes it here.**

---

## Task 1: The fault engine — modifiers, not branches

§3.5's six faults: feeder starvation, outfeed blockage, joining-force drift, carrier wear, lane contamination, optics fouling.

**A fault must be a modifier on the number, applied where the number is computed.** The alternative — `if fault_active: ...` at each station — puts scenario knowledge in four places, makes two simultaneous faults ambiguous, and means the plant behaves differently in a way a reader can see. §3.5's whole premise is that the analysis has to find the fault from its consequences, so the consequences must arise from the same code path as normal behaviour.

**Files:** Create `plant/simulator/src/simulator/faults.py`, `tests/test_faults.py`. Modify `config.py`.

**Interfaces:** `FaultKind` (str enum, six members); `Fault(kind, at: timedelta, params: Mapping[str, float], until: timedelta | None)`; `FaultSet` with `.active_at(sim_ts) -> tuple[Fault, ...]` and `.modify(quantity: str, value: float, at: datetime, **context) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_fault_outside_its_window_changes_nothing() -> None:
    """The identity property. Every fault is a no-op before its offset and after its
    end, so a run with a scenario loaded but not yet fired is byte-identical to a run
    with none — which is what makes the ground-truth log's timestamps meaningful."""


def test_two_faults_on_one_quantity_compose_rather_than_race() -> None:
    """§3.5 allows a scenario to stack. If the second silently replaced the first, a
    scenario script would behave differently depending on declaration order."""


def test_carrier_wear_reads_the_carrier_and_lane_contamination_reads_the_lane() -> None:
    """A modifier that ignored its context would apply to every part, which is
    exactly the difference between scenario 4 (one carrier) and a line-wide drift —
    and scenario 4's whole diagnostic value is that it concentrates."""


def test_the_same_seed_and_scenario_reproduce_the_same_modifications() -> None:
    """§3.6. Seed with zlib.crc32, never hash()."""
```

- [ ] **Steps 2-6:** implement, pass, `make check`, commit — `feat(plant): six faults as modifiers on the numbers they change`

---

## Task 2: The noise floor, and the one number that decides M2c

§3.5's five components: micro-stops every ~20 min; baseline scrap ~1.5% from a distribution unrelated to any injected fault; operator interventions with realistic acknowledge delays; false accepts and rejects **at the classifier's configured rates (D7 — already built)**; and genuine carrier-to-carrier variation that is **not** a fault.

**The last item is the point, and it carries the load-bearing number.** Carriers draw a baseline quality once per run from a seeded distribution; scenario 4's wear on carrier 7 accumulates *on top of* it. The ratio between baseline spread and injected wear decides whether finding carrier 7 is a `GROUP BY` (too tight) or impossible (too loose).

**Declare that ratio as configuration with its reasoning, and measure where it actually sits.** It is why M3 needs a significance test at all, and a plan that ships it unmeasured has moved the hard part into M3 without saying so.

**D13: delete `_next_takt`'s resample guard here.** It exists only because M1's takt was constant and asyncua drops unchanged values. Once micro-stops and jitter make takt genuinely variable the guard is dead code pretending to be a safety property — **but verify the reconciliation still balances after removing it**, because the guard is what made row counts exact by construction.

**Files:** Create `plant/simulator/src/simulator/noise.py`, `tests/test_noise.py`. Modify `config.py`, `stations/base.py`, `line.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_carrier_quality_varies_without_any_fault_injected() -> None:
    """Without this, finding carrier 7 is a GROUP BY and §3.5's significance
    requirement is decoration."""


def test_one_worn_carrier_is_distinguishable_from_the_baseline_spread() -> None:
    """The measurement that decides whether scenario 4 is findable. Assert a
    separation, and RECORD the measured effect size in the commit — if the two
    distributions overlap too far the ratio is wrong and M3 inherits an
    unwinnable scenario."""


def test_micro_stops_are_brief_and_clear_themselves() -> None:
    """§3.5: brief jams every ~20 min. A micro-stop that needed an operator would be
    a Held, not noise, and would pollute every cause-candidate query."""


def test_the_baseline_scrap_rate_is_unrelated_to_any_injected_fault() -> None:
    """§3.5's words. If baseline scrap drew from the same stream as a fault, a
    scenario's signal would be partly its own noise."""
```

- [ ] **Steps 2-6.** Commit — `feat(plant): a noise floor that makes the analysis work for it`

---

## Task 3: Scenarios, declarative

§3.5's eight. A scenario is a list of `(offset, fault, params)`, fired from a script during catch-up to build history, or by hand from the HMI while live.

| # | Scenario | What it must produce |
|---|---|---|
| 1 | feeder starvation upstream of S1 | S2–S4 starve **in sequence**, not together |
| 2 | outfeed blocked after S4 | blockage propagates **back** to S1 |
| 3 | joining force drifts down at S2 | `gap` rises, alarm, S2 aborts |
| 4 | carrier 7 wears | `misalignment` + `scratch` **concentrate on carrier 7**, no stop |
| 5 | feeder lane 2 contaminated | `missing_part` + `contamination` **on lane 2 only** |
| 6 | optics fouling | confidence decays across **all six** classes, scrap rate flat |
| 7 | lane 1 gets a bad lot | `gap` rises with the **joining force perfectly stable** |
| 8 | one defective component | **one** bad part, not a lot pattern |

**Scenario 7 is the strongest and the easiest to get wrong.** Its symptom — rising `gap` — is exactly what a joining-force drift produces. The only thing separating them is that the defects correlate with the *lot* rather than the force, and M2b's curve makes that separable: the contact point moves while the clamp force does not. **Assert that the force stream is stable across scenario 7**, because a scenario that also drifts the force has quietly become scenario 3.

**Scenario 8 is its mirror** and exists to stop over-generalising: one defective component must be one bad part, not a lot problem.

**§3.5 says scenario 6 is the weakest, and D8 is why it is not.** The simulator renders genuinely degraded images when fouling is active and the classifier derives its confidence from an image statistic — so the decay is caused rather than declared. **That is a real change to `render.py` and `classifier.py`**, and the honest limit goes in a comment: a statistic-to-confidence formula is still a formula, so this moves the stipulation one layer down rather than removing it.

**Files:** Create `plant/simulator/src/simulator/scenarios.py`, `tests/test_scenarios.py`. Modify `render.py`, `classifier.py` (D8), `config.py`, **`line.py` and the stations**.

**Scenarios 1 and 2 need a gate this milestone has not built yet.** Tasks 1–2 bought the modifier and the number it moves: starvation drives `LaneFill` to 0 and blockage drives `OutfeedFill` past capacity — **and nothing stops S1 or S4.** Measured with both at full magnitude, the only suspends on the line are the ordinary buffer ones; S1 keeps feeding with a lane at 0 and S4 keeps discharging with the outfeed at 107 against a capacity of 50.

The gate belongs beside `Line._suspend_reason`, which is where every other "this station cannot run" decision already lives. Without it, scenario 1 cannot produce "S2–S4 starve in sequence" and scenario 2 cannot produce "blockage propagates back to S1" — the two consequences their table rows exist to demand.

- [ ] Steps. Commit — `feat(plant): the eight scenarios, and the one whose symptom points at the wrong cause`

---

## Task 4: Ground truth

§3.6: JSONL on the gt volume carrying run id, seed, clock configuration, every injected fault with simulated timestamps and **expected observable consequences**, and the true defect state of every part.

**The expected-observable-consequences field is what lets M2c assert without diagnosing.** Scenario 1 records *"S2 Suspended/starved, then S3, then S4, in that order, within N s"*; Task 7 asserts that sequence appears in `state_changes`. Not that anything diagnosed it.

**The isolation guard already exists** — M1's `test_no_stack_mounts_a_volume_another_stack_owns` names this volume in its own docstring as the case it was written to catch. Add only the positive half, mirroring `test_the_plants_history_volume_is_mounted_only_by_the_plant`.

**Determinism is a ground-truth property.** §3.6: same seed plus same scenario reproduces the run exactly. Assert a **byte-identical** log across two runs — that is cheap, very strong, and catches any unseeded draw anywhere in the plant.

**Files:** Create `plant/simulator/src/simulator/ground_truth.py`, `tests/test_ground_truth.py`. Modify `plant/compose.yml`, `tests/test_compose_invariants.py`, `server.py`.

- [ ] Steps. Commit — `feat(plant): a ground-truth log, on a volume the diagnostics stack cannot reach`

---

## Task 5: Alarms, and migration 004

§3.3: `Aborted` is a fault shutdown and a **cause candidate**; `Held` needs an operator. §3.7 gives the HMI active alarms with acknowledge buttons. §5.2's table: `id · station_id · code · text · severity · raised_at · acked_at · cleared_at`.

**Alarms are the plant's own claim about itself, and that makes them a trap.** §3.3 warns that identifying the root cause as "the first station that raised an alarm" would be circular — the simulator produces exactly that, and the analysis would merely find it again. It does not apply at all to the two scenarios whose cause lies outside the line, where no alarm is raised anywhere. **Say that in the migration comment**, because `alarms` is the table most likely to be mistaken for an answer.

**Files:** Create `Migrations/004_m2c.sql`, `plant/simulator/src/simulator/alarms.py`. Modify the address space (an alarm event type), `Subscriptions.cs`, `PostgresWriter.cs`, `hmi.py`, tests.

Established constraints: a cache in the writer may only hold what a committed transaction put there; `INSERT ... SELECT ... WHERE NOT EXISTS` for any `SMALLSERIAL`; page size from the policy; the truncation guard covers new event types; `backfill_windows.stream` is `<station code>.<signal>`; read `Historizing` and skip what the plant does not historise.

- [ ] Steps. Commit — `feat: alarms, and why the first station to raise one is not the answer`

---

## Task 6: The HMI injects faults and acknowledges alarms

§3.7: active alarms with acknowledge buttons, and a fault-injection panel. This is the **demo console** and the screen the plant is driven from.

**The HMI has been read-only until now, deliberately.** M2b declined a hold button because fault injection without a ground-truth log would be exactly this milestone's job done without its evidence. Now the log exists, so injection is legitimate — **and every injection from the panel must write to ground truth, the same as a scripted one.** An injection that does not is a fault the evaluation can never account for.

`plant-hmi` joins `plant-net` and `plant-edge`. Never `field-net`.

**Files:** Modify `hmi.py`, `plant/hmi/src/`, tests.

- [ ] Steps. Commit — `feat(plant): a panel that injects faults, and records every one`

---

## Task 7: What M2c proves

**Each scenario runs, writes its injection and its expected observable consequences to ground truth, and those consequences are asserted present in Postgres.** Eight scenarios, eight assertions about consequences — and **no assertion that anything diagnosed anything.**

The four hardest to assert honestly, and why:
- **1 and 2** are about *sequence*: S2 then S3 then S4, not all three. A test that asserts only "all suspended" would pass on a line with no buffers at all.
- **4 and 5** are about *concentration*: the defects must be significantly more frequent on carrier 7 / lane 2 than elsewhere. Assert a measured separation, not a presence.
- **6** is about *all six classes falling together* while the scrap rate stays flat — the softmax-impossible shape §3.4 exists to allow.
- **7** is about the force stream being **stable** while `gap` rises. That is the assertion that proves the scenario is not secretly scenario 3.

Update `measurements/authenticity/README.md`: M2c makes the ground-truth and determinism proofs, and moves *"computed root cause matches ground truth"* from "waits on M2c + M3" to "waits on M3" — the ground truth it needed now exists.

**Files:** Create `plant/simulator/tests/test_scenario_consequences.py`. Modify `measurements/authenticity/README.md`, `README.md`, `Makefile` (an `m2c-demo`).

- [ ] Steps. Commit — `test(plant): eight scenarios, eight consequences, and nothing diagnosed`

---

## Self-review

**Spec coverage:** §3.5's six faults → Task 1; its noise floor → Task 2; its eight scenarios → Task 3; §3.6's ground truth and determinism → Task 4; §3.3's alarms and §5.2's table → Task 5; §3.7's panel → Task 6; the proofs → Task 7. D8 → Task 3. D13 → Task 2.

**The line:** no task diagnoses anything. Task 7 asserts consequences, never causes.

**Carried hazards:** seed with `crc32`; the ORDER MATTERS generator sequence for any new event type; a writer cache holds only what committed; the truncation guard must cover new event types; read `Historizing`; and the milestone's own recurring failure — **a test that asserts a fault was injected rather than that its consequence appeared is testing the injector.**
