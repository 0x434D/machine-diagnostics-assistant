# §1's authenticity proofs — what is proved, and what is not yet provable

§1 sets one standard: **every link has a proof that would fail if the link were a facade.**
Nine links are named there. This file exists so that the ones nobody can prove yet are
written down in one place rather than inferred from the absence of a test — a missing
proof and a passing one look identical from a green pipeline.

Each proof below says where it runs, or which milestone it waits on and why.

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

## Not provable yet

| § | Link | Waits on | Why not yet |
|---|---|---|---|
| 1.5 | Analysis | M2c + M3 | the computed root cause has to match the simulator's ground truth. M2c is what produces a ground-truth log at all (§3.6); M3 is what does the matching. The plant runs a fixed nominal takt today with no injected faults, so there is no root cause to compute and nothing to compare against |
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
