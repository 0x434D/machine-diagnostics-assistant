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

### M2a adds two of its own

| Claim | Proof | Runs as |
|---|---|---|
| Propagation is real and measured: stop S2 and S3 starves once B2_3 drains — not before, and not in sympathy | `plant/simulator/tests/test_propagation.py` | `make check` **and** `make verify` |
| `read_rows == pg_rows` per stream at §4.1's 25 streams, not M1's three | the gateway's own `/reconcile`, over an explicit window | `make m2a-demo` step 5, and by hand — below |

The propagation proof is in process and takes under a second, so excluding it from the
gate would cost nothing and would lose the one test that fails if §3.1's propagation
claim stops being true. It derives its expected delay from the level B2_3 is holding at
the moment S2 stops; §3.1's "roughly 30 s" is that level times S3's takt, and in steady
state the level is 4 or 5, so the delay is 24–32 s. A test asserting 30 would prove the
constant rather than the plant.

The reconciliation is made by asking `/reconcile` for the window the backfill ledger
actually covers, rather than for its default:

```
psql -c "SELECT min(from_ts), max(to_ts) FROM backfill_windows"
curl "localhost:${GATEWAY_PORT:-8080}/reconcile?from=<min>&to=<max>"
```

Bounding the window at `max(to_ts)` is what turns the default's one-sided claim into the
two-sided one: rows the live subscription wrote land after it, so stored no longer
exceeds read for a reason that has nothing to do with loss. Measured on a full 33 h
boot, 2026-09-14: **26 streams, 402,044 rows read, 402,044 stored, 0 lost, 0
duplicates, 0 recorded gaps.** `S3.State` and `S4.State` read zero, and that is correct
rather than missing — the plant's own ledger holds 9 and 11 rows for them, all of them
bring-up and start-up transitions from the nine minutes of simulated history that
precede the gateway's window. The bottleneck and the station behind it did not change
state once in 19,800 parts.

**`measurements/run_r1_r2.py` is not what makes this claim, and was not repaired here.**
It is M1's runner: it names three unqualified streams, and M2a's ledger qualifies a
stream by station (`S2_Joining.TaktTime`), so its ledger query matches nothing and its
stored query sums four stations into one count. It fails in the safe direction — it
reports FAIL on a healthy run — and it is left that way deliberately. Repairing it by
re-deriving the SQL would put a second copy of `Reconciler.StoredAsync` in Python:
five tables, a settled-transitions view and a `raw_events` DISTINCT, whose way of going
wrong is to report a clean R1 while the gateway stores something else. Calling
`/reconcile` instead — the shape above — would also mean moving R1's criterion from
`read_rows == pg_rows` to `lost == 0`, because at a larger page count F2's
page-boundary duplicates are expected rather than loss. That is a change to what R1
passes on, and it belongs to the spec owner rather than to the task shipping the
milestone's proof. `measurements/r1-r2-results.json` and `make m1-report` therefore
still describe M1: three streams, an M1 gateway, and the risk table M1 closed.

## Not provable yet

| § | Link | Waits on | Why not yet |
|---|---|---|---|
| 1.5 | Analysis | M2c + M3 | the computed root cause has to match the simulator's ground truth. M2c is what produces a ground-truth log at all (§3.6); M3 is what does the matching. The plant runs a fixed nominal takt today with no injected faults, so there is no root cause to compute and nothing to compare against |
| 1.6 | Agent | M4 | the claim is behavioural — the agent says "I have no data for that window" rather than inventing. M1 tests the empty-window *response*, which is the endpoint's contract, not the agent's judgement. The scripted provider cannot be asked whether a model would resist inventing |
| 1.7 | Tool layer | M4 | there is no MCP server. "The same tools, the same results" needs two callers to compare |
| 1.8 | Identity | M5 | there is no identity layer, so every diagnostics endpoint answers unauthenticated requests. The README says so in those words, and that is the whole of the current posture |
| 1.9 | Traceability | M2b + M7 | a containment query returns the affected serials scored against ground truth: M2b creates the serials and the genealogy, and M7 is the harness that scores. Neither exists |

**Ground truth never reaches the diagnostics stack**, so every proof in this column is
scored outside both stacks or not at all. That is the invariant, not an obstacle to route
around: a proof made by giving the analysis the answer proves nothing.

## What `make verify-no-gaps` claims, and what it does not

It calls the gateway's `/reconcile` with its **default** window, which ends at `UtcNow`
and therefore includes rows the live subscription wrote that no backfill window ever
claimed. Stored is then *greater* than read, `StreamReconciliation.Lost` clamps at zero,
and what the endpoint reports is:

> nothing that was read was lost, and no window is recorded as missing.

That is a real claim — it is what catches a write path that drops rows and a gap the
gateway knows about — and it is **not** the claim that nothing was missed. Nothing inside
the diagnostics stack can make that one: the count of what the plant actually holds is on
the other side of a boundary that carries OPC UA and nothing else (§4.5). The three-way
comparison — plant ledger, what the backfill read, what Postgres stores — belongs to
`measurements/run_r1_r2.py`, which sits outside both stacks and is the row above.
