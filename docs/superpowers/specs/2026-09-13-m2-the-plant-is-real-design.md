# M2 — The Plant Is Real — Design

Status: design agreed, ready for implementation planning
Date: 2026-09-13
Follows: `2026-09-12-machine-diagnostics-assistant-design.md` (the spec), M1 as merged

---

## What this document is

The main spec stays the source of truth for **what** M2 builds — §§3.1–3.7, §4.1 and §5.2
already describe the plant, the address space and the data model. This document fixes
**how**, and cuts M2 into three releasable plans.

It exists because §13 gives M2 a single line, and that line names four independent
subsystems plus a new frontend. M1 needed a 4,387-line plan for one station, two signals
and one event. Planning M2 as one unit would produce something nobody could review and no
releasable point until the end.

---

## 1. Three plans, sequenced

Each is separately demoable and separately committable. Each carries its own gateway and
schema work rather than leaving a pile of unread streams for M3.

### M2a — the line runs

Four stations under PackML, joined by three buffers, with twelve carriers circulating,
producing through catch-up and live.

**Done:** stop S2 and S3 starves once B2_3 drains — §3.1's specific claim, ~30 s at
capacity 5 and 6 s takt, *measured rather than asserted* — visible on the HMI and recorded
in `state_changes` with the buffer named. `pg_rows == plant_rows` still exact at the new
stream count.

### M2b — every part has a name

Component serials and lots per lane, assembly serials created at S1 and marked at S2,
as-built genealogy, and S2's press recorded against the serial at the instant it happens.

**Done:** §14's traceability line — any serial traced end to end: genealogy, station
history, the process values recorded for that part, inspection result, disposition. The
read path performs no time-range join, extending M1's existing guard.

### M2c — the line misbehaves on purpose

Six fault types, eight declarative scenarios, the permanent noise floor, ground-truth
JSONL, alarms.

**Done:** each scenario runs, writes its injection and its *expected observable
consequences* to ground truth, and those consequences are asserted present in Postgres.

**M2c deliberately stops short of diagnosis.** Whether the analysis reaches the right
cause is M3; scoring it is M7. Without this line M2c quietly absorbs M3.

### Ingest rides along

§13 calls M2 "THE PLANT IS REAL" and does not mention the gateway until M3. Taken
literally that produces data nothing reads, and makes M3 into "ingest at scale" plus "the
analysis is real" — two milestones wearing one label.

It also leaves §12's stated compounding unmeasured. All three truncation defects M1 found
scale with stream count, and they were findable *only* by reading at scale through the
gateway. So each plan carries its own subscriptions, its own migration and its own
reconciliation:

| Plan | Migration | Adds |
|---|---|---|
| M2a | `002` | `buffers`, `carriers`, `state_changes`, `buffer_levels` |
| M2b | `003` | `component_lots`, `components`, `assemblies`, `genealogy`, `part_station_events`, `part_process_values`, `part_process_curves`, `part_dispositions`; widens `inspection_results` |
| M2c | `004` | `alarms` |

---

## 2. The scale M2 actually reaches

§15 says M2 grows "nine more signal streams". §4.1's own node list does not support that,
and the number matters because M1's three truncation defects scale with it.

Counting §4.1 as written: S1 nine variables, S2 six, S3 four, S4 six, three buffers with
four nodes each — **37 variable nodes.**

| Kind | Count | Treatment |
|---|---|---|
| Static topology — `Capacity`, `UpstreamStation`, `DownstreamStation` | 9 | read on connect, never historised |
| Live-only — `Lane1_Lot`, `Lane2_Lot`, `CurrentAssemblySerial` | 3 | see D12 |
| **Historised streams** | **25** | 2 exist in M1; 23 are new |
| Event types | 5 | 1 exists in M1; 4 are new |

§15's figure is wrong by roughly a factor of three and should be corrected rather than
planned against.

---

## 3. Decisions

Each records what would overturn it, because a decision whose failure mode is unstated is
an assumption wearing a decision's clothes.

### D1 — the line advances on a discrete-event queue, not four async tasks

One priority queue of `(next_event_time, station_index)`, popped in time order, ties broken
by index.

§3.6 requires that the same seed plus the same scenario reproduces the run exactly. Four
concurrent `asyncio` loops drawing from a shared RNG and racing on buffer levels make that
a property you hope for rather than one you get. A fixed global tick is deterministic but
quantises every timestamp, turning the step size into a hidden precision limit on every
interval the analysis later measures — and station takts are independently jittered anyway.

The queue also collapses catch-up and live into one mechanism: catch-up drains it as fast
as it can, live sleeps until each event's wall-clock equivalent. That extends M1's
phase-based clock rather than replacing it.

*Overturned by:* a scenario requiring genuinely simultaneous station behaviour that cannot
be expressed as ordered events.

### D2 — `station_s3.py` splits before it grows

Today it is 358 lines holding the takt loop, catch-up generation, live production and S3's
specifics together. Four stations multiply all four concerns.

```
line.py           the event queue, buffers, the carrier pool
packml.py         the 15 states, and the guard that every Suspended carries
                  a buffer id *and* a direction (§3.3 makes that field non-optional)
stations/base.py  takt loop, signal writing, ledger accounting
stations/s1_feeding.py  s2_joining.py  s3_inspection.py  s4_outfeed.py
```

### D3 — one mounted JSON signal-policy file, failing open

`GatewayOptions.TaktDeadband` is a single scalar today, and `Subscriptions.cs` hardcodes
which signal receives it. M2 has three kinds of stream: counters where a deadband silently
loses parts, noisy floats where a deadband is the point, and `State`/`StateReason` strings
where every transition matters.

Policy is a file mapping signal name to `{deadband, page_size}`, with defaults by kind.
Not environment variables — 25 streams of them, for a signal set that is *discovered*
rather than known in advance.

**The default for an unknown signal is record-everything, never skip.** Topology discovery
means the gateway will meet signals no policy names. A policy that fails closed loses a
stream and passes every test.

Rejected: letting the plant declare deadband hints on its own nodes. It inverts who owns
the policy, and a real plant's metadata would not be ours to trust.

### D4 — page size becomes per-event-type

`HistoryEventPageSize = 25` exists solely because S3's inspection events carry images
against a 4 MiB response limit. The four new event types carry no images. Paging them at
25 costs ~3,200 round trips per stream where 80 would do.

The truncation guard M1 built — a full final page with no continuation point is treated as
truncated, and the window halves — must cover every new stream, at every page size.

### D5 — the HMI's WebSocket is served in-process by the simulator

FastAPI and uvicorn are already resolved in the plant workspace by the inspection service,
so no new version enters the dependency graph; they are new to the `simulator` package and
the commit message must say so.

In-process means no second source of truth and no state-sync problem, and fault injection
from the HMI reaches the scenario engine directly. A separate container polling
`simulator.status` would make the snapshot interval the HMI's frame rate and still need its
own channel back in.

Rejected: the HMI as a second OPC UA client. It puts a third member on a network the
invariant says has exactly two, and fault injection is a write, which the plant must never
accept over UA.

`plant-hmi` joins **`plant-net`**, never `field-net`.

### D6 — the force–distance curve gets its own table

§5.2's `part_process_values` is `assembly_serial · station_id · signal · value` — a scalar.
§3.4a is emphatic that the curve, not the two summaries, is what separates scenario 7 from
scenario 3.

`part_process_curves (assembly_serial, station_id, signal, samples DOUBLE PRECISION[])`,
one row per part, ~19,800 in a full history. `part_process_values` keeps peak force and
joining distance exactly as §5.2 says. The alternative — 40 rows per part in long form —
is ~800,000 rows and a text column doing an index's job.

### D7 — the classifier owns false-accept and false-reject, not the noise model

§3.4 assigns them to the classifier; §3.5 lists them under the noise floor. §3.4 wins: a
real `ModelClassifier` has error rates *emergently*, so putting them in the noise model
double-counts them the day the interface is swapped. §3.5 describes them as a property of
the line, which they are — but the producer is the classifier.

Closes assumption A6 from the M1 plan.

### D8 — inspection confidence is computed from the rendered image

§15 asks whether scenario 6 survives review, given that its confidence decay is stipulated
rather than emergent. As specced, optics fouling can only work by setting a knob that says
"be less confident now", because `SimulatedClassifier` never looks at the image.

Instead: the simulator renders genuinely degraded images when fouling is active, and the
classifier derives its confidence scalar from an image statistic — contrast or local
variance. **The verdict still comes from the truth side channel**; no trained model is
implied and §3.4's interface is unchanged.

Stated honestly: a statistic-to-confidence formula is still a formula someone wrote, so
this moves the stipulation one layer down rather than removing it. What it buys is that
the fouling becomes a real cause with a real effect, and the confidence field starts
carrying information about the thing it claims to describe.

### D9 — historian retention stays unbounded

`period=None, count=0`. The trap is real and under-documented: asyncua deletes rows where
`SourceTimestamp < now() − period` using the **real** clock, while the plant writes
simulated timestamps, so any retention shorter than the history depth erases history as it
is written. At 25 streams the file is still small. M2a measures it.

Closes assumption A4.

*Overturned by:* the historian file becoming large enough to affect catch-up or the image.

### D10 — the reject rate reverts to §3.5's 1.5%

M1 used 5% as a declared measurement knob for R4.

**The side effect this decision claimed does not exist, and M2a measured it.** The claim was
that rejects are the only parts that render an image, so dropping the rate would cut
catch-up's largest cost by roughly 70%. Rejects are the only parts that *carry* an image —
every part is rendered regardless, because the classifier needs one to classify. The rate
decides which images ride the event, not how many are drawn.

Measured over two boots at 25 streams on one idle machine: **183.1 s at 5% against 182.3 s at
1.5%.** The wall did not move. What moved is what crosses the wire and lands in storage:
carried images 939 → ~295, image bytes 103 MB → ~32 MB.

The decision stands — 1.5% is §3.5's noise floor and that is reason enough — but it buys a
third of the ingest volume, not a third of the boot. Recorded because Task 1's own reasoning
leaned on the false version when it predicted catch-up would get cheaper.

Closes assumption A16.

### D11 — `inspection_results` widens once, in M2b

M1's event carries a single `DefectClass` and a scalar `Confidence`. §3.4 requires six
independent per-class scores that do not sum to 1, and scenarios 4 and 5 each need **two**
classes on one part; pattern DP-02 is keyed on a pair. The M1 shape cannot express either.

The table reaches §5.2's full target shape in one ALTER — `carrier_id`,
`defect_classes[]`, `confidences[]`, `positions[]` — in M2b, where the per-part data model
is settled. M2c then only produces the data. Two ALTERs otherwise: one for `carrier_id`
when carriers arrive, one for the arrays when scenarios do.

This deliberately leaves M2a's inspection results without a carrier link, even though
carriers exist by then. Nothing is lost: history is regenerated from scratch on every
boot, and the only question that needs the link — scenario 4's carrier wear — is M2c's.

### D12 — a variable duplicated by an authoritative event is live-only

`CurrentAssemblySerial`, `Lane1_Lot` and `Lane2_Lot` restate what `AssemblyCreatedEvent`
and `ComponentReadEvent` already carry. The event is authoritative — that is §3.4a's whole
argument — so historising the variable creates a second, weaker copy of the same fact and
invites exactly the time-join the spec forbids.

They stay in the address space, readable live by the HMI. They are not historised.

### D13 — `_next_takt`'s resample guard is deleted in M2c

It exists only because M1's takt was constant and asyncua's monitored-item filter drops
unchanged values. Once the noise floor makes takt genuinely variable, the guard is dead
code pretending to be a safety property. Planned rather than left to ossify.

---

## 4. M2a in outline

**Task 1 is a measurement**, the way M1 opened with R1–R4. One number cannot be predicted
from M1's results: historian write throughput during catch-up at 25 streams rather than 3.

The reasoning for expecting *improvement* is real — rendering dominated the 151–185 s wall,
and D10 cuts rendered images by ~70% — but §12 predicts compounding, and this project's
standard is to have the number rather than the argument. It also settles D9's file size.

Then: PackML and the state machine; buffers and the starved/blocked determination; the
carrier pool; the four stations on the event queue; topology discovery extended to read
buffer references and fill `buffers`; migration `002`; the thin HMI.

**The HMI at M2a** is the minimum that makes the line legible while you build it: four
stations coloured by state, buffer fill bars, clock panel. §15 banks an idea for M6 —
colour by *category* (producing, waiting-on-others, held-by-own-fault, stopped,
transitioning) rather than by state. It costs nothing to adopt now, it is the exact
distinction the project exists to make, and §15 appears to have filed it against the wrong
frontend. Colour is never the only channel.

---

## 5. M2b in outline

Component serials and lots per lane; assembly serial created at S1, marked at S2; the four
new event types; genealogy; migration `003`; D11's widening; the HMI gains a part strip.

**The curve model needs two independent knobs.** §3.4a: *where* force begins to rise is
about the incoming components — an undersized component lets the press travel further
before meeting resistance — while *how* load develops after contact is about the press.
Scenario 7 (bad lot, stable force) and scenario 3 (force drift) must be separable by curve
shape while peak and distance agree.

Get this wrong and scenario 7 is unwinnable by construction — and §3.5 calls it the
strongest test in the set precisely because the symptom points at the wrong cause.

---

## 6. M2c in outline

Six fault types as modifiers on normal behaviour rather than special-case branches;
scenarios as declarative `(offset, fault, params)`; the noise floor; ground-truth JSONL;
alarms and migration `004`; the HMI gains alarm acknowledge and the fault-injection panel.

**The noise floor has one load-bearing number.** §3.5's last item is genuine
carrier-to-carrier variation that is *not* a fault. Carriers draw a baseline quality once
per run from a seeded distribution; scenario 4's wear on carrier 7 accumulates on top of
it. The ratio between baseline spread and injected wear decides whether finding carrier 7
is a `GROUP BY` (too tight) or impossible (too loose). It is configuration, declared with
its reasoning. It is also why M3 needs a significance test at all.

**Ground truth.** JSONL on the gt volume: run id, seed, clock configuration, every injected
fault with simulated timestamps and expected observable consequences, and the true defect
state of every part.

The *expected observable consequences* field is what lets M2c assert without diagnosing.
Scenario 1 records "S2 `Suspended`/`starved`, then S3, then S4, in that order, within N s";
M2c asserts that sequence appears in `state_changes`.

**The isolation guard already exists.** M1's `test_no_stack_mounts_a_volume_another_stack_owns`
names M2's ground-truth volume in its own docstring as the case it was written to catch. M2c
adds only the positive half, mirroring `test_the_plants_history_volume_is_mounted_only_by_the_plant`.

---

## 7. What M2 proves, and what stays pending

§1's two outstanding proofs both stay outstanding: M2 supplies the ground truth, M3 does
the matching, M7 does the scoring. M2's proofs are its own.

| Proof | Plan |
|---|---|
| Propagation is real and measured — stop S2, S3 starves once B2_3 drains | M2a |
| `pg_rows == plant_rows` exact at 25 streams, extending R1 to the scale §12 says compounds it | M2a |
| Genealogy is as-built — the read path performs no time-range join | M2b |
| Determinism — same seed and scenario produce a byte-identical ground-truth log | M2c |
| Ground-truth isolation — no diagnostics container mounts the gt volume | M2c |

`measurements/authenticity/README.md` — which M1's plan Task 15 marked complete but never
committed — is written in M2a, listing every §1 proof still pending and the milestone each
waits on.

---

## 8. Changes this makes to the spec

Landing as one revision commit before any plan is written, so three plans are not written
against text known to be wrong.

- **§15** — "nine more signal streams" corrected to §4.1's real count (§2 above)
- **§15** — scenario 6's open question closed by D8; the Python 3.13/3.14 revisit-at-M2 note resolved
- **§5.2** — gains `part_process_curves` (D6); `inspection_results` reaches its stated shape in M2b (D11)
- **§3.4** — the classifier owns false-accept and false-reject (D7); confidence is derived from the image (D8)
- **§3.5** — false-accept/false-reject cross-referenced to §3.4 rather than owned
- **§13** — M2 recorded as three sequenced plans, with ingest inside each

---

## 9. Deferred, with the reason

- **Significance test choice** — M3, and it needs the noise floor's real distribution, which M2c is what produces
- **Chart library, visual language** — M5/M6 per §15; the HMI is deliberately undesigned, as the M1 chat box was
- **Diagnosis of any scenario** — M3. M2c proves the fault happened and was recorded
- **Containment scoring** — M7, which owns the harness
