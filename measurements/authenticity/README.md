# §1's authenticity proofs — what is proved, and what is not yet provable

§1 sets one standard: **every link has a proof that would fail if the link were a facade.**
Nine links are named there. This file exists so that the ones nobody can prove yet are
written down in one place rather than inferred from the absence of a test — a missing
proof and a passing one look identical from a green pipeline.

Each proof below says where it runs, or which milestone it waits on and why.

**Seven of the nine are proved after M4, and two are not — and one of the seven is proved in
half.** 1.1 to 1.4 were M1's; M3 closed **1.5**, the analysis. **M4 closes 1.7** outright and
**1.6 only as far as anything without an API key can**: the mechanism that stops an invention
reaching the reader is proved, and whether a *model* resists inventing is not, because
everything M4 built runs against `ScriptedProvider`. That split is written out in full below
and is the honest boundary of this milestone — read past it and this file starts claiming
something §1 does not have. What remains: **1.8** (identity) waits on **M5**, because there is
no identity layer to prove anything about — every diagnostics endpoint still answers
unauthenticated requests, and what M5 has to supply is an identity on the request and a proof
that an unauthenticated one is refused; **1.9** (traceability returns *exactly* the affected
serials) waits on **M7**, because "exactly" is a score against ground truth — M2b proved the
genealogy resolves and M3 wrote the query, and what is missing is the harness that compares
the returned set against what the plant recorded, outside both stacks, since ground truth
never reaches this one. The one non-§1 row, `read_rows == pg_rows`, waits on a runner outside
both stacks and is argued at the bottom of this file.

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
| §3.5's eight scenarios, each seeded as the **consequences** it declares and never as its fault, each answered by M3's endpoints: the chain's root and category, the dimension a quality problem concentrates in, and the two rows that have the same symptom and different answers | `diagnostics/analysis/tests/test_analysis_proof.py` | `make check` |

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
| 4 | `/stops`, `/inspection/patterns` | **no stop, and no micro-stop.** With the worn carrier at the pool's **median** and §3.5's own 33 h depth: the carrier dimension **tested inside the defect class** reports `carrier 7 × misalignment` (12/1100 = 1.09 % against 0.283 %, adjusted *p* = 0.023) and **nothing else out of 108 pairs**, while the plain carrier dimension reports carrier **18** and leaves carrier 7 at adjusted *p* = 0.159, `not_significant` |
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

**What the measurement showed, and the dimension it forced.**

*§5.5's four dimensions could not answer row 4, and the fifth is now there.* Row 4's
consequence is `CLASS_CONCENTRATES` — `misalignment` and `scratch` on carrier 7 — and the
carrier dimension tests each carrier's **whole reject rate**, diluting two of six raised
classes into all six. So `/inspection/patterns` now also tests the carrier **within each
defect class**: carrier 7's `misalignment` rate against the other carriers' `misalignment`
rate, with the multiplicity family every stratum's comparisons together (18 × 6 = 108
hypotheses, because "does any carrier concentrate any class" is one question). It is a
dimension §5.5 happened not to list, in exactly the sense the lot dimension already was, and
the row that demanded it is the one row §3.5 states as a pair.

*What that bought, measured on this generator at the pool's median placement.* The plain
dimension's verdict on carrier 7 moves with the draw; the stratified one does not.

| parts per carrier | plain carrier dimension | carrier within class |
|---|---|---|
| 600 | 7, 18 | — |
| 700 | 7 | — |
| 800 | 7, 18 | **7 × misalignment** |
| 900 | 18, 7 | **7 × misalignment** |
| 1,000 | 18, 7 | **7 × misalignment** |
| **1,100** (§3.5's own 33 h) | **18** — and not 7 | **7 × misalignment** |
| 1,300 | 18, 7 | **7 × misalignment** |
| 2,000 | 18, 7, 3, 2, 1 | **7 × misalignment** |

The smallest depth at which the cross holds is **800 parts per carrier**, two thirds of the
plant's own history. Below that it correctly reports nothing. Above it, it reports the worn
carrier and the class it wore, alone, at every depth tried — while the plain dimension
alternates between naming carrier 7 and not, and by 2,000 parts per carrier is naming five
carriers because the noise floor's own carrier-to-carrier variation has itself become
detectable (which `significance.py` already measured from the other direction).

*The placement mattered more than the wear, and that is why it was taken out.* An earlier
round of this proof placed the worn carrier where the shipped seed drew it — `test_noise`'s
2.18 % `misalignment | scratch` against the pool's 0.571 %, which with the 2.86 × wear divided
out is **1.34 ×** the pool baseline before anything was injected. Scenario 4 passed there on
the plain dimension, and it was passing on the seed's luck. Put at the pool's median it does
not, and placed three ranks below it — quality 0.872, the seventh of eighteen and an entirely
ordinary carrier — the plain dimension never names carrier 7 at any depth from 600 to 2,000
while the cross names it from 1,300. A capability that depends on
where a draw put the worn carrier is not a capability, so the fixture places it at the median
and the proof asserts the cross.

*Carrier 18 is the wrong finding and not a false one.* It is the top of §3.5's own quality
spread and genuinely scraps more than the rest, so reporting it is what `patterns.py` says
Benjamini-Hochberg's false-discovery bound means. That is a different complaint from a false
positive, and it is the complaint §3.5 row 4 exists to make: a line has a worst carrier
whether or not anything is wrong with it, and the answer to "which carrier is wearing" has to
be more specific than "the worst one".

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

**In `make check`, not behind `make verify`.** It starts no container of its own — it runs in
process against the Postgres the analysis suite already uses — and the whole file is **8 s**,
of which the largest single cost is seeding §3.5's 33 h of history for scenario 4 (2.7 s,
19,800 parts and their genealogy; every other fixture is under a second). That is the same
shape as the propagation, traceability and consequence proofs, all three of which are in the
gate for the same reason.

§1's container-dependent proofs stay where they are and the marker set is not collapsed; this
one file moved to where its cost says it belongs. §8.4's argument is the whole of the reason:
*"That suite runs in seconds, costs nothing, has no variance, and catches the failures that
matter most … it is the suite most easily forgotten."* A proof that runs only when somebody
deliberately looks is one nobody sees fail, which is the definition of forgotten.

### M4 closes §1.7, and §1.6 as far as a milestone with no API key can

| Claim | Proof | Runs as |
|---|---|---|
| §1.6 — the agent says *"I have no data for that window"* rather than inventing: an empty window yields no `measured` finding, a hypothesis with no evidence strength cannot ship, a citation Postgres cannot open is stripped after §6.5's one retry, a contradiction with no reasoning is refused, and an ingest gap is named beside the count | `diagnostics/agent/tests/test_authenticity.py` | `make verify` |
| §1.7 — one question over both bindings agrees; one analysis query answers alike over MCP and REST; and an external client following the SOP it read by URI reaches the figures the pipeline stated | `diagnostics/mcp/tests/test_authenticity.py` | `make verify` |

Both start a Postgres container, apply the gateway's own migrations, and serve the real
services over loopback sockets — the analysis service as the `analysis` role over `read.*`,
the agent over `POST /ask`, the MCP server over Streamable HTTP at the revision §6.11 pins.
Nothing below either is a fake, and that is also why they are behind `make verify` rather than
in the gate. The rule this file has applied since M2a is cost, not category: the propagation,
traceability, consequence and analysis proofs are all in `make check` because they run in
seconds against a container the gate already starts, and §1's own four are held back because
they stop and restart containers. These two start one of their own, so they belong with the
second group.

Measured on the run that closed these rows: `make authenticity` green end to end, **16 proofs
over four packages** — plant 4, analysis 4 (6 min 12 s, the container stops and restarts),
agent 5 (3.4 s), MCP 3 (5.0 s). The two new files are seconds of assertions behind a container
that takes most of a minute to start, which is the whole of why they are not in the gate.

**`make verify` runs four packages now, and ran two before.** `agent/pyproject.toml` and
`mcp/pyproject.toml` have registered the `authenticity` marker since the tasks that created
them — declared, excluded from `addopts`, and run by nothing, which from a distance is
indistinguishable from wiring. The two lines in the `verify` target are what M4 adds, and they
could not have been added earlier: `pytest-marked` treats an empty selection as a failure,
deliberately, so the wiring is only safe once the proofs exist.

#### What §1.6 does **not** establish

**Whether a model resists inventing when the data is thin.** There is no API key in this
environment. Every one of the proofs above runs against `ScriptedProvider`, which is keyword
matching wearing the interface of a model and says so in every answer it produces. A scripted
provider cannot be asked whether a model would resist inventing; it can only be asked whether
the guards hold when something invents on purpose — which is what these proofs ask, by
injecting the invention themselves.

So the claim M4 closes is: **a fabricated answer cannot reach the reader.** The claim it does
not close is: **a model, handed thin data, does not fabricate.** The mechanism is proven; the
judgement is not. §8.1's case classes are where the second one is scored, by a harness that
can read the figures, against a provider that is a model — M7 for the harness, and a key for
the model.

*The pipeline, stage by stage, against the scripted provider:*

| §6.1 stage | What a pass proves |
|---|---|
| 2 window · 3 coverage · 4 routing · 6 verification · 6.5 composition | **proven without a model.** Code decides all of it; the provider has no say and cannot change the outcome |
| 1 classification · 5 tool choice | **mechanism proven, judgement unproven.** That an unclassifiable question falls back with a caveat, that every operation is reachable, that a tool error returns to the model — yes. That a *model* would read this sentence as `quality_investigation`, or reach for `inspection_patterns` here — no test in this project can say |

That table is the pipeline author's own and the reviewer confirmed it; it is carried here
verbatim rather than restated, because the boundary is the point. `agent/tests/test_pipeline.py`
carries it too, beside the tests it describes.

**Every guarantee here is of the form *a badly formed answer cannot ship*. None is of the form
*a badly formed answer is not produced*.**

#### What was falsified, against what

Each break below was applied to the shipped code and the proof it targets re-run.

| Break | Proof that failed |
|---|---|
| the coverage guard removed — stage 3 no longer short-circuits on an empty window | 1.6's empty window (the answer no longer opens *"I have no data for that window"*; the scripted provider's own `total == 0` branch still refused to count, which is the second line and not the one §1.6 names) |
| `validate_basis` returns without checking `evidence_strength` | 1.6's hypothesis (`DID NOT RAISE`) |
| `citations.keep` returns every finding unverified | 1.6's citation (the invented serial's claim shipped, and the composer summarised it) |
| `Contradiction._check_reasoning` returns `self` | 1.6's contradiction (`DID NOT RAISE`) |
| the ingest-gap caveat dropped from stage 3 | 1.6's gap |
| `diagnose` returns a summary instead of the answer object | 1.7's agreement |
| the MCP tool binding drops the window's `to` parameter | 1.7's query parity **and** 1.7's SOP walk |
| `core/` excluded from the resource listing | 1.7's SOP walk (CORE-01 and CORE-02 unreachable — the two documents §6.2 never leaves to retrieval) |

The seventh row is the one that shows the three §1.7 proofs are not one proof written out
three times: a binding that loses a query parameter changes what an external client gets and
changes nothing about whether `diagnose` and `POST /ask` agree.

#### §1.7, stated at its real strength

*"Drive the same question two ways and assert the answers agree"* is the weakest of the three
and is written down as such. `diagnose` calls the agent's own `POST /ask` — deliberately, and
`mcp_server/diagnose.py` argues why: there must be exactly one agent, with one configuration
and one audit trail. So the two callers reach one pipeline in one process, and **agreement is
close to guaranteed by construction.** It is not evidence that two implementations converged,
because there is only one.

What it does establish is that the binding hands the answer object over **unchanged** — every
finding, every citation, every caveat, the method and the provider that produced it — rather
than helpfully summarising it into a tool result, which is the one thing §6.3 exists to
prevent and the easiest thing for a wrapper to break. Put beside `mcp/tests/test_parity.py`,
which holds the served MCP tool list against the live OpenAPI operation set in **both**
directions, that is the real content of the claim: one surface, two bindings, one answer.

The other two proofs are not guaranteed by construction. An MCP `tools/call` and a REST `GET`
share nothing below the transport, so a binding that dropped an argument or lost a field fails
the second. And the third is §6.11's own sentence carried out rather than asserted: the client
reads the procedure the answer says it followed — by URI, discovered from the resource
listing rather than spelled here — carries out SOP-05's first two steps with the tools the
server advertises, and finds that every number the pipeline put in front of a reader is a
number it obtained for itself. That is SOP-05's own failure condition (*"a number in the prose
that does not appear in any tool result"*) checked from outside, by a client that was given a
document and a tool list and nothing else.

**What §1.7 does not establish**, in the same words as above: that a *model* driving this
binding would follow the procedure it read, or choose those tools. The binding is proven; the
judgement of whatever binds to it is not.

## Not provable yet

| § | Link | Waits on | Why not yet, and what it needs |
|---|---|---|---|
| 1.8 | Identity | M5 | there is no identity layer, so every diagnostics endpoint answers unauthenticated requests. The README says so in those words, and that is the whole of the current posture. What closes it: an identity on the request, `sessions.subject` carrying the OIDC `sub` it is already typed for, and a proof that an unauthenticated request is **refused** — the half a login screen does not give |
| 1.9 | Traceability | M7 | **the genealogy and the query both exist.** M2b creates the serials, the supplier lots and the as-built links and proves above that a serial resolves to its whole history without inference; M3 writes the containment query. What is left is the word *exactly*: no miss and no false inclusion is a **score** against what the plant recorded, and ground truth never reaches this stack. What closes it: M7's harness, running outside both stacks, comparing the returned set against the scenario's own ledger |
| 1.6's behavioural half | Agent | M7, and an API key | the mechanism is proved above and the judgement is not. §8.1's case classes are the scoring, and a provider that is a model is the thing being scored. Listed here rather than left out, because "1.6 is closed" would otherwise read as more than it is |
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
