# §1's authenticity proofs — what is proved, and what is not yet provable

§1 sets one standard: **every link has a proof that would fail if the link were a facade.**
Nine links are named there. This file exists so that the ones nobody can prove yet are
written down in one place rather than inferred from the absence of a test — a missing
proof and a passing one look identical from a green pipeline.

Each proof below says where it runs, or which milestone it waits on and why.

**Five of the nine are proved after M3, and four are not.** 1.1 to 1.4 were M1's; **M3 closes
1.5**, the analysis, and closes nothing else. What remains: **1.6** (the agent does not invent)
and **1.7** (the tool layer answers two callers alike) wait on **M4**, which is where a model
and an MCP server first exist; **1.8** (identity) waits on **M5**, because there is no identity
layer to prove anything about; **1.9** (traceability returns *exactly* the affected serials)
waits on **M7**, because it is a scored answer and M7 is the harness that scores. M3 wrote
1.9's query and that is all it could do — the comparison is against ground truth, which never
reaches this stack. The one non-§1 row, `read_rows == pg_rows`, waits on a runner outside both
stacks and is argued at the bottom of this file.

## Provable today

| § | Link | Proof | Runs as |
|---|---|---|---|
| 1.1 | OPC UA server | a foreign client connects and browses the address space | `test_a_foreign_client_can_browse_the_address_space` · `make browse` |
| 1.2 | Edge gateway, downstream | stop Postgres → the gateway buffers → restart → nothing lost | `test_the_gateway_buffers_while_postgres_is_down_and_loses_nothing` |
| 1.3 | Edge gateway, upstream | stop the plant → reconnect → `HistoryRead` closes the outage | `test_history_read_closes_an_upstream_outage` |
| 1.4 | Stack independence | the diagnostics stack answers with the plant shut down | `test_the_diagnostics_stack_answers_with_the_plant_shut_down` |

All four are in `diagnostics/analysis/tests/test_authenticity.py`, marked `authenticity`,
and run by `make verify`. They stop and start real containers and take minutes, which is
why they are not in `make check`.

That last sentence is also how all four stopped running without anything turning red.
M2a Task 9 made the gateway's signal policy mandatory — `SignalPolicy.Load` raises rather
than defaulting, because a gateway that silently subscribes to everything at no deadband
is a different gateway from the configured one — and the fixture that starts the proofs'
own gateway was not given the mount. It exited 139 on its first line and the fixture
reported "gateway never reached live", which reads like a slow plant. Nothing in the gate
covers `make verify`, so §1's four proofs were unrunnable for a milestone and the only way
to find out was to run them. **Run `make verify` when a gateway requirement changes,**
and read the container's own last words: the fixture now prints them.

### M2a adds one of its own

| Claim | Proof | Runs as |
|---|---|---|
| Propagation is real and measured: stop S2 and S3 starves once B2_3 drains — not before, and not in sympathy | `plant/simulator/tests/test_propagation.py` | `make check` **and** `make verify` |

**One, not two.** M2a's plan named a second — `read_rows == pg_rows` per stream at
§4.1's 25 streams — and it is not provable from inside the diagnostics stack. It is in
the next table with the reason.

The propagation proof is in process and takes under a second, so excluding it from the
gate would cost nothing and would lose the one test that fails if §3.1's propagation
claim stops being true. It derives its expected delay from the level B2_3 is holding at
the moment S2 stops; §3.1's "roughly 30 s" is that level times S3's takt, and in steady
state the level is 4 or 5, so the delay is 24–32 s. A test asserting 30 would prove the
constant rather than the plant.

### M2b adds one too, and it is §1's traceability half

| Claim | Proof | Runs as |
|---|---|---|
| Any serial resolves to its whole history — two component serials with their supplier lots, the press curve, the verdict with its class vector, the disposition — **and the read path issued no time-range join** | `diagnostics/analysis/tests/test_traceability.py` | `make check` |

§14 asks that any serial be traceable end to end and §3.4a says how: the per-part record is
written at the instant of production and is never reconstructed by joining the time series on
"which part was at S2 at 02:14:07". That difference is invisible in a passing response —
both readings return a number — which is exactly the shape §1 exists for, so the proof is
built so that a facade returns something else.

**It is two assertions about one request, and each covers the other's blind spot.** The
content half resolves a serial the test asks the database for rather than one it chose, and
checks every section of §14's line. The method half reads Postgres's own `log_statement`
output and requires each of the six statements the trace issued to name that serial among its
bound parameters and to compare no time column at all — because a time-range join must bind a
window instead of a serial, which is what a time-range join *is*. Content alone would pass
against a reconstruction that happened to guess right; the statements alone would pass against
a read path that asked perfectly and returned nothing.

Supporting tests in the same file make the reconstruction *wrong* rather than merely
forbidden, so that the value assertions have teeth: the fixture seeds S2's historised streams
densely across the window in a range that does not overlap anything a part carries, and seeds
two assemblies created at the same instant with different components and different press
records.

**What was falsified, against what.** Each facade below was applied to the shipped code and
the suite re-run; the tests named are the ones that failed and no others.

| Facade | Tests that failed |
|---|---|
| `_process_values` rewritten as a time-range join over `signals` | `test_the_press_record_is_the_parts_own_and_not_the_time_series`, `test_two_parts_made_at_the_same_instant_keep_their_own_histories`, the proof |
| `_process_curves` returning nothing — the shape a reconstruction is forced into, since the time series carries nothing a stroke can be rebuilt from | `test_the_curve_is_the_one_the_press_recorded_for_this_serial`, the proof, and three section tests |
| `/inspection/stats` back to the scalar `defect_class` group-by | `test_the_breakdown_reads_the_score_vector_rather_than_the_dead_scalar`, `test_a_reject_no_class_can_explain_is_counted_rather_than_dropped` |
| a time predicate on the genealogy query that changes **not one returned value** | the proof, alone |

The last row is the one that shows the two halves are independent: nothing a caller can see
changed, every content assertion passed, and only the statement half failed. The third row is
a separate defect from the first two and its failures come from the stats revert, not from any
time-range join — an earlier revision of this file, and commit `f65afa4`'s message, ran them
together and described all four failures as one falsification. They are not, and the curve
test in particular does **not** fail against the `part_process_values` rewrite; it reads a
different table. The table above is what was actually observed.

**It runs in `make check`, not behind `make verify`**, for the same reason the propagation
proof does: it is in process against the Postgres container the analysis tests already use
and takes seconds. What `verify` holds back is the proofs that stop and restart containers.
The half this cannot make is the end-to-end one — that the rows it reads were put there by
the real gateway from the real plant — and nothing in `make check` claims it.

### M2c adds two, and the second is the one that keeps §1.5 honest

| Claim | Proof | Runs as |
|---|---|---|
| Each of §3.5's eight scenarios records what it injected and what should follow, and **those consequences are in §5.2's tables** — eight scenarios, twelve consequences, and no assertion that anything diagnosed anything | `plant/simulator/tests/test_scenario_consequences.py` | `make check` |
| The same seed and the same scenario reproduce the run **byte for byte** (§3.6) | `test_a_run_is_reproducible_byte_for_byte_from_its_seed`, `test_a_different_seed_writes_a_different_log` | `make check` |

Both are in the gate rather than behind `make verify`, for the reason the propagation proof
is: they run a `Line` in process against the Postgres container the gate already starts, and
the whole module takes 66 s.

**The consequence proofs assert the plant, not the transport.** The rows they query are the
plant's own published output loaded into §5.2's schema — the gateway's own migration files,
applied unchanged, so the columns and the constraints are the ones the analysis will read —
but they did not travel over OPC UA and the gateway did not write them. §1.2 and §1.3 own
that half. Saying so here is cheaper than a reader assuming otherwise, and it is the same
boundary `read_rows == pg_rows` runs into from the other direction.

**What each of the eight asserts, and what it measured.** Every number below came out of
Postgres on the run the test performs.

| # | Asserted out of | Measured |
|---|---|---|
| 1 | `state_changes` | S2 +19.5 s → S3 +41.9 → S4 +46.1, **strictly** in order, each on the buffer feeding it, inside the 180 s the log claims |
| 2 | `state_changes` | S3 +30.0 s → S2 +37.0 → S1 +49.8, the same three buffers the other way |
| 3 | `signals`, `alarms`, `state_changes` | `JoiningForcePeak` 4214.3 → 3791.9 N, **10.7 σ** of its own 39.4 N spread; A-207 raised +2902 s; `Aborted` 0.5 s *after* its own alarm |
| 4 | `inspection_results` | carrier 7 at **d = +5.92** leave-one-out, against the best rival in the same run at +1.25 and the worst carrier of a clean twin at **+2.57** — and the clean twin's `ORDER BY rate DESC LIMIT 1` still answers carrier 10 |
| 5 | `inspection_results` | the weakest named class **2.95×** the strongest unnamed one, against the clean twin's **0.84×** on the identical query |
| 6 | `inspection_results` | all six class scores fall together to **0.554** of themselves, matching the fault's own 0.55 clarity factor; and **48 of 2,093 parts rejected against the clean twin's 48 of 2,093 — not one verdict moved**, serial by serial |
| 7 | `inspection_results`, `signals` | 14 gaps in the lot's 500 parts against a 0.27 % baseline outside it — **10.9 σ**, where the clean twin's identical window reaches **2.3**; and the force moves **0.059 σ**, against scenario 3's 10.7 |
| 8 | `inspection_results` | **exactly one** of the 11 parts inspected over the fault's window plus one buffer transit carries `gap`, against **none** of the clean twin's 11 over the same window; that part is `A-00000355`, and it is also the one part the twin difference names |

Five of the eight load a **clean twin** of the same run into a second schema, because "carrier
7 stands out from what" and "the verdicts did not move compared to what" have no meaning
without a contrast group. That twin is a property of the proof and never of a deployment: a
real history holds one run, which is why §3.5 row 3's `gap` consequence was dropped from the
ground-truth log in the round before this one rather than rewritten as a paired claim. So each
of the five has a form the run itself supports, and the twin either calibrates the bar (4, 5,
7) or turns a statistical statement into an exact one (6, 8).

Scenario 8 is where that rule was stated and not kept until this round: both halves of its
difference read the clean schema, so a consequence recorded in the ground-truth log had no form
a single history could check — the exact property row 3's `gap` was dropped for. It now claims
**one gapped part over the stretch one press can reach**, which is eleven parts wide at the
shipped settings and which a clean run answers by chance at the 0.2706 % baseline scenario 7
measures: 1 − (1 − 0.002706)¹¹, near three per cent. The twin says *which* part, and is no
longer what the claim rests on.

**What was falsified, against what.** Each break below was applied to the shipped code and the
proof it targets re-run — not the whole module, so "and no other failed" is not claimed here.

| Break | Proof that failed |
|---|---|
| S1's feeder gate returns `None` — scenario 1 injects and nothing starves | 1 (`S2_Joining ended the run Execute, not Suspended`) |
| S4's outfeed gate returns `None` | 2 |
| scenario 1 claims its chain in the reverse order | 1 (`the chain did not reach the stations in order`) |
| scenario 2 claims its chain in the reverse order | 2 |
| the clamp drift never reaches the press (the `JOINING_CLAMP_FORCE` modifier dropped) | 3 (`fell 1.1 against a 39.4 spread (0.0 σ)`) |
| `alarm_consecutive_parts` raised to 10,000 — the drift runs and no alarm is raised | 3 |
| `carrier_wear_sigmas` 3.0 → 0.4 | 4 (`d=1.66: the answer is inside the noise`) |
| `lane_contamination_factor` 6.0 → 1.0 | 5 (`0.84× the strongest unnamed one`) |
| `clarity=` deleted from `InspectionClient.produce` — the fouling never reaches the frame | 6 (`gap did not fall at all (1.000)`) |
| `truth_by_lane`'s propensity divided by `clarity²` — the fouled lens also damages parts | 6 (`43 parts were disposed of differently with the lens fouled`) |
| the same mutation at `clarity` rather than `clarity²` | 6 (`19 parts were disposed of differently`) |
| scenario 7 keeps its bad lot **and** drifts the clamp — it becomes scenario 3 | 7 (`moved 9.53 σ across the window`) |
| a fault is never repaired (`until` ignored) — scenario 8 becomes a bad lot | 8 (`144 parts gained ['gap']`) |
| `defective_component_mm` 2.0 → 0.6, scenario 7's lot magnitude — the one part is no longer certainly gapped | 8 (`0 of the 11 parts inspected … carry ['gap']`) |
| one checker removed from the dispatch table | 8, and the coverage test |
| `SCENARIO = "operator"` — every scripted injection stamped as an operator's | 1, and the `source` test |

**The two `truth_by_lane` rows are the ones this table was missing, and scenario 6's
`SCRAP_RATE_FLAT` was the only one of the twelve consequences absent from it.** That was not a
clerical gap: the assertion could not fail. Measured against the form it had — the 300-part
warmup against the 595 parts after the ramp, two-proportion at three pooled standard errors —
the bar sits at 2.727 pp on a 2.000 % baseline, and the 300-part side is what sets it, so
running deeper does not narrow it. The `clarity²` mutation **tripled the reject rate to
6.218 %, 2.79 standard errors, and passed**. Against the clean twin the comparison is a fixed
draw either side and therefore exact, and both mutations now name the parts that moved.

What the twin does **not** establish is the classifier's half — that a fouled lens costs
certainty and not the verdict on a real image. That is
`plant/inspection/tests/test_inspection.py::test_a_fouled_lens_costs_certainty_and_not_the_verdict`,
on the real classifier, where it belongs: the two workspaces may not import each other (§10.7).

The last two rows are the ones this milestone kept re-learning. A consequence with no checker
is written to the log, skipped by the dispatcher and reads as a scenario whose claims all held —
so the dispatcher raises on an expectation it does not know, and a separate test holds the
table against §3.5's rows in **both** directions. And `source` is asserted as the literal
`"scenario"`, not against the constant that produces it: the previous round's fix moved with
the constant and survived the same mutation.

### M3 closes §1.5, and it is the one §8.4 says matters most

| Claim | Proof | Runs as |
|---|---|---|
| §3.5's eight scenarios, each seeded as the **consequences** it declares and never as its fault, each answered by M3's endpoints: the chain's root and category, the dimension a quality problem concentrates in, and the two rows that have the same symptom and different answers | `diagnostics/analysis/tests/test_analysis_proof.py` | `make verify` |

§8.4: *"Propagation correctness is where the system's truth actually lives, and it is
directly checkable: inject a known fault, assert the computed chain against ground truth …
It is more important than the LLM evaluation, and it is the suite most easily forgotten."*
This is that suite. No model is involved in any of it, which is why it has no variance and
why every number below is a fact rather than a sample.

**The seeding rule is the whole of what makes it worth anything.** `scenarios.py` declares
twelve consequences per §3.5's rows and deliberately names no cause — *"writing the expected
cause here would make every one of M7's numbers circular"* — and the same applies one
milestone on. So each fixture carries PackML transitions with the buffer each one names,
class rates that rise on a carrier or a lot, a clamp stream that falls or does not, and
confidences that decay while no verdict moves. The ground-truth log is never read; it is on
a volume no diagnostics container mounts and `test_compose_invariants.py` is what keeps it
that way.

**What the analysis computed.** Every number is out of the endpoints on the run the test
performs.

| # | Answered by | Computed |
|---|---|---|
| 1 | `/stops`, `/stops/{id}` | **`external_upstream`**, rooted at **S1** on `starved:feeder`, through all three buffers in order — S4 ← B3_4 ← S3 ← B2_3 ← S2 ← B1_2 ← S1, terminating `line_edge` with no cause candidate |
| 2 | the same | **`external_downstream`**, rooted at **S4** on `blocked:outfeed`, in **one link** — the three stations backing up behind it are consequences of the same blockage and the walk is right not to climb them |
| 3 | the same | **`internal`**, rooted at **S2** in `Aborted`, terminating `cause_candidate` with S2 the only candidate — while an **older, still unacknowledged alarm stands at S4**, downstream of the root and raised 60 s before S2's own. `/signals/trend` puts the clamp **10.3 σ** below where it started |
| 4 | `/stops`, `/inspection/patterns` | **no stop, and no micro-stop.** Carrier **7 significant** at adjusted *p* = 0.0023 (34/1100 against 1.50 % expected) — and carrier 18 beside it on the identical count, which is not a false finding: see below |
| 5 | the same | no stop; the classes significantly **above** expectation are exactly `missing_part` and `contamination` (*p* = 0.0056 and 1.8 × 10⁻⁶), and the **lane dimension carries no verdict at all**, refused in §3.5's own words |
| 6 | `/stops`, `/inspection/stats`, `/inspection/patterns` | no stop; the scrap rate holds (1.50 % → 1.35 %) while the breakdown empties — **25 of 27 rejects carry no class the threshold can name**, against 0 of 15 before the fouling. No defect class, no time bucket and no lot is a pattern |
| 7 | `/inspection/patterns`, `/signals/trend`, `/alarms` | no stop, no alarm; **`gap` is the one class above expectation** (*p* = 2.7 × 10⁻⁶); the **lot** `L-1-01` is significant (24/500 against 2.18 %, *p* = 0.0136) and is the only significant value anywhere in carrier, lot or time; **no time bucket reaches a verdict**; the force moved **0.15 σ** |
| 8 | the same | **nothing significant in any dimension** — 13 lots tested, every one of them past the sample gate, every one `not_significant`. The one defective part is still findable: `/parts/affected?defect_class=gap` returns it |

**Rows 7 and 8 are the pair, and they came out the right way round.** Their symptom is
identical and it is also scenario 3's: rising `gap`. DP-01 attributes that to a drifting
joining process at S2, and for both of these rows that is the wrong answer. The proof asserts
the two halves of not giving it — the S2 force trend does not move (0.15 σ against scenario
3's 10.3) and the lot dimension does. M3 stops there: naming the lot as *the cause* is
interpretation and belongs to M4. What M3 must not do is assert the mechanical answer, and
what it must do is surface the evidence that separates them.

The two are seeded from one generator under one name, so both runs draw identically about
every part and differ only in what was injected. That is what makes "the lot is significant
in one and nothing is significant in the other" a statement about the injection rather than
about two seeds.

**Three things the measurement showed that the plan did not predict.**

*Scenario 4 depends on where in the pool the worn carrier sits, and the dependence is total.*
`/inspection/patterns` tests each carrier's **reject rate**; §3.5 row 4's consequence is
class-scoped (`misalignment | scratch`, `CLASS_CONCENTRATES`), and the dilution is 3:1. At
the pool's median the wear lifts a carrier's reject rate by 1.6 × inside a pool whose own
qualities span 3 ×, and **no depth separates it** — measured at 1,100, 1,600 and 2,200 parts
per carrier, where the only carriers reported are the pool's own extremes. `test_noise`
measures the shipped seed's carrier 7 at 2.18 % `misalignment | scratch` against the pool's
0.571 %, so with the 2.86 × wear removed it was already at **1.34 ×** the pool — and it is
that placement, not the wear alone, that makes it findable. The fixture places it there and
says so. **A carrier the plant wore at the median would be invisible to this endpoint**, and
the fix is a dimension §5.5 does not have: carrier × class. `/parts/affected?carrier=&defect_class=`
already returns the counts to cross by hand.

*Depth matters as much.* Even so placed, carrier 7 reaches a verdict only at the plant's own
33 h history depth (1,100 parts per carrier). At 400 and 700 per carrier the same fixture
reports nothing — correctly, and `NOT_SIGNIFICANT` is the right answer there, but a shift-long
window will not find a worn carrier on this line.

*A true finding is not always the finding asked for.* Carrier 18 is significant in scenario 4
beside carrier 7, and is the **only** finding in scenario 6. It is the top of §3.5's own
quality spread and genuinely scraps more than the rest, so reporting it is exactly what
`patterns.py` says Benjamini-Hochberg promises — the share of reported findings that are
false is bounded, and this one is not false. The proof asserts that carrier 7 is among the
findings and that there are at most two, rather than that it is alone: a fixture tuned until
the noise floor went quiet would be a fixture with the noise floor taken out.

**Where scenario 7's lot beats the clock, and why that is arranged.** A 500-part lot inside
60-minute buckets sits almost entirely in one of them unless the window is placed so that a
bucket boundary cuts the lot in half. The fixture opens at **:45** for exactly that reason,
and it is not a thumb on the scale: it is the arrangement in which *"the defects follow the
lot"* and *"the defects follow the clock"* are different claims at all. On a window opening
on the hour the same run reports the time bucket more strongly than the lot, which is a true
statement about that window and a useless one for telling scenario 7 from a bad hour.

**What it does not establish**, in the same words M2c's section uses: the rows it reads are
seeded, not carried over OPC UA by the gateway. §1.2 and §1.3 own that half. What is new here
is only that the *computing* is real, which is the half §1.5 was waiting for.

**Marked `authenticity`, and the case for moving it is stronger than for the four above.** It
starts no container of its own — it runs in process against the Postgres the analysis suite
already starts — and the whole file is **8 s**, of which the largest single cost is seeding
§3.5's 33 h of history for scenario 4 (2.4 s of it, 19,800 parts and their genealogy; every
other fixture is under a second). That is the same shape as the propagation, traceability and
consequence proofs, all three of which are in `make check`. It is left behind `make verify`
for this round only because §1's proofs are read as a set and splitting the set is a decision
with an owner; the numbers say it belongs in the gate, and the argument against it is
currently nothing but consistency with a marker.

## Not provable yet

| § | Link | Waits on | Why not yet |
|---|---|---|---|
| 1.6 | Agent | M4 | the claim is behavioural — the agent says "I have no data for that window" rather than inventing. M1 tests the empty-window *response*, which is the endpoint's contract, not the agent's judgement. The scripted provider cannot be asked whether a model would resist inventing |
| 1.7 | Tool layer | M4 | there is no MCP server. "The same tools, the same results" needs two callers to compare |
| 1.8 | Identity | M5 | there is no identity layer, so every diagnostics endpoint answers unauthenticated requests. The README says so in those words, and that is the whole of the current posture |
| 1.9 | Traceability | M7 | **the genealogy it was waiting on exists.** M2b creates the serials, the supplier lots and the as-built links, and proves above that a serial resolves to its whole history without inference — which is the half §3.4a is about. What is left is the *scoring*: "returns exactly the affected serials" is an answer compared against ground truth, and M7 is the harness that compares. M3 is what writes the query |
| — | `read_rows == pg_rows` at 25 streams | a working `measurements/run_r1_r2.py` | it is a three-way comparison — the plant's ledger, what the backfill read, what Postgres stores — and only the runner outside both stacks can make it. See below |

**Ground truth never reaches the diagnostics stack**, so every proof in this column is
scored outside both stacks or not at all. That is the invariant, not an obstacle to route
around: a proof made by giving the analysis the answer proves nothing.

## Why `read_rows == pg_rows` is not provable from inside the stack

An earlier revision of this file listed it as provable, by asking `/reconcile` for the
window `backfill_windows` actually covers rather than for its default. That was wrong,
and wrong in this project's signature way — **the check was in the code but not in the
path that decided.** `ReconciliationResult.Reconciled` is
`Gaps.Count == 0 && all(Lost == 0)`, and `Lost` is `Math.Max(0, …)`. Bounding the
window changes what is *printed*; the exit condition is the same one `verify-no-gaps`
uses, and a surplus — stored exceeding read — cannot fail it either way. Nothing was
comparing the two numbers.

Asserting the equality instead would be worse, because the surplus is legitimate. A
reconnect runs the backfill a second time, so `min(from_ts) … max(to_ts)` unions two
passes and contains the live stretch between them: rows the subscription wrote that no
window ever claimed. §1's own proofs cause exactly that, by stopping the plant.
Measured on two boots: **402,044 read / 402,044 stored** on a clean single-pass 33 h
boot, and **402,044 read / 427,571 stored** on one that had reconnected — both healthy.
A target asserting equality would call the second gateway broken.

So the equality needs the plant's own ledger, which is on the far side of a boundary
carrying OPC UA and nothing else (§4.5). That is what `measurements/run_r1_r2.py` is
for, and why leaving it broken has a cost rather than being free.

`make backfill-counts` prints the per-stream comparison over the bounded window, which
is worth seeing — at 26 streams "reconciled: true" says nothing about which stream holds
what — and its exit code is honestly the `reconciled` one, gaps and losses only:

```
psql -c "SELECT min(from_ts), max(to_ts) FROM backfill_windows"
curl "localhost:${GATEWAY_PORT:-8080}/reconcile?from=<min>&to=<max>"
```

On the clean 33 h boot, `S3.State` and `S4.State` read zero, and that is correct rather
than missing: the plant's own ledger holds 9 and 11 rows for them, all bring-up and
start-up transitions from the nine minutes of simulated history that precede the
gateway's window. The bottleneck and the station behind it did not change state once in
19,800 parts.

## `measurements/run_r1_r2.py` — decided, not repaired

It is M1's runner: it names three unqualified streams, and M2a's ledger qualifies a
stream by station (`S2_Joining.TaktTime`), so its ledger query matches nothing and its
stored query sums four stations into one count. It also starts its gateway with no
signal policy mounted, which since Task 9 is a container that exits 139 on its first
line. It fails in the safe direction — FAIL on a healthy run — and it is left that way
deliberately.

Repairing it by re-deriving the SQL would put a second copy of
`Reconciler.StoredAsync` in Python: five tables, a settled-transitions view and a
`raw_events` DISTINCT, whose way of going wrong is to report a clean R1 while the
gateway stores something else. Calling `/reconcile` instead would mean moving R1's
criterion from `read_rows == pg_rows` to `lost == 0`, which is a change to what R1
passes on and belongs to the spec owner rather than to the task shipping the milestone's
proof. **The cost of that decision is the row above: M2a ships one authenticity proof of
its own rather than two.** `measurements/r1-r2-results.json` and `make m1-report` still
describe M1 — three streams, an M1 gateway, the risk table M1 closed.

## What `make verify-no-gaps` claims, and what it does not

It calls the gateway's `/reconcile` with its **default** window, which ends at `UtcNow`
and therefore includes rows the live subscription wrote that no backfill window ever
claimed. Stored is then *greater* than read, `StreamReconciliation.Lost` clamps at zero,
and what the endpoint reports is:

> nothing that was read was lost, and no window is recorded as missing.

That is a real claim — it is what catches a write path that drops rows and a gap the
gateway knows about — and it is **not** the claim that nothing was missed. Nothing inside
the diagnostics stack can make that one — see the two sections above, which is the same
boundary reached from the other direction.
