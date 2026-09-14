# Machine Diagnostics Assistant — Design

Status: design agreed, ready for implementation planning
Date: 2026-09-12

---

## 1. Purpose and standard

Build the full chain — machine → edge → storage → analysis → AI agent → application —
end to end, and have every link be genuinely real rather than pretended.

The measure of success is not "I can explain every line without AI". It is:

> **Every link holds what it claims. Each one has an authenticity proof: a test that
> would fail if the link were a facade.**

Examples of what that means in practice, all of which become tests:

| Link | Authenticity proof |
|---|---|
| OPC UA server | a foreign client (UaExpert) connects and browses the address space |
| Edge gateway, downstream | stop Postgres → the gateway keeps buffering → restart → no gaps |
| Edge gateway, upstream | stop the plant stack → reconnect → `HistoryRead` closes the outage |
| Stack independence | the diagnostics stack answers questions with the plant stack shut down |
| Analysis | computed root cause matches the simulator's ground truth, with no model involved |
| Agent | it says "I have no data for that window" instead of inventing an answer |
| Tool layer | an external MCP client reaches the same tools and gets the same results |
| Identity | an unauthenticated request to any diagnostics endpoint returns 401, MCP included |
| Traceability | a containment query returns exactly the affected serials — scored against ground truth, no misses, no false inclusions |

The machine itself is simulated — that is the premise, not a concession. Everything
downstream of the OPC UA interface is real software doing real work.

### Non-goals

- No real PLC, no writes to the plant, ever
- No trained vision model in this build; images are rendered and classification is
  simulated behind a real, swappable interface
- No multi-tenancy; one plant, one user directory
- No user self-registration, password reset or email verification — the identity
  provider owns all of that and we build none of it
- No `SignAndEncrypt` on OPC UA and no certificate-based *user* authentication;
  transport signing with mutual certificate trust only (§4.6)
- No agent framework (no LangChain/LangGraph), no vector database
- Not production-ready, and the README says so in those words

---

## 2. System shape

Two independent Docker stacks and one referee that belongs to neither.

```
═══════════════════════════════════════════════════════════════════════
  PLANT STACK                     docker compose -f plant/compose.yml up
═══════════════════════════════════════════════════════════════════════

   plant-hmi ·TS ──── ws ────► line-simulator ·PY
    line overview                · PackML state machines, 4 stations
    buffers, alarms              · buffers, carriers, feeder lanes
    fault injection              · simulated clock: past → catch-up → live
    live part images             · OPC UA SERVER  ← the only exposed port
                                        │
                                        │  inspect(image, part_context)
                                        ▼
                                 inspection-service ·PY
                                  · renders the part image
                                  · SimulatedClassifier   ← default
                                  · ModelClassifier       ← drop-in, later
                                        ▲
                                        └─ truth side channel,
                                           never in the request

   ground-truth run log ─────────────────────────────────► [ gt volume ]

───────────────────────────────────────────────────────────────────────
        ▼   field-net  ·  OPC UA only  ·  read-only  ·  never writes
───────────────────────────────────────────────────────────────────────

═══════════════════════════════════════════════════════════════════════
  DIAGNOSTICS STACK         docker compose -f diagnostics/compose.yml up
═══════════════════════════════════════════════════════════════════════

   edge-gateway ·C#
    · session + reconnect, subscription management, deadband
    · HistoryRead backfill, durable local queue, overflow detection
    · raw landing + normalised model, gap markers, /status
          │
          ▼
   postgres ◄──────────── analysis-service ·PY
          ▲                · OpenAPI contract — single source of truth
          │                · facts + propagation with derivation, on demand
          │                        ▲
          │            ┌───────────┼───────────┐
          │            │           │           │
          └──── agent-service ·PY  │      mcp-server ·PY
                 · staged pipeline │       · tools     → analysis queries
                 · knowledge/*.md  │       · resources → SOPs, catalogue
                 · diagnose() ─────┘       · diagnose()→ full pipeline
                        ▲                    Streamable HTTP, localhost
                        │ REST
                 diagnostics-ui ·TS
                  chat · evidence · charts · timeline · trace

═══════════════════════════════════════════════════════════════════════
  EVAL HARNESS                    part of neither stack — the referee
═══════════════════════════════════════════════════════════════════════
   reads [ gt volume ]  ·  drives the public API  ·  scores both axes
```

### 2.1 The boundary contract

One protocol, one endpoint, one direction. The diagnostics stack subscribes and reads
history; it never writes, never calls methods, and has no other channel into the plant.

Three networks make this enforceable rather than merely intended:

- `plant-net` — internal to the plant stack
- `diag-net` — internal to the diagnostics stack
- `field-net` — an **external** Docker network that exactly two containers join:
  the simulator and the edge gateway

A fourth container on `field-net` means the architecture has been violated, and
`docker network inspect` shows it.

The plant HMI talks to the simulator over a WebSocket inside `plant-net`. That does not
cross the boundary; real machines have their own HMI on their own network.

**Hard requirement:** the diagnostics stack starts, runs and answers questions with the
plant stack completely down. It answers from history.

### 2.2 Why the referee sits outside

The simulator knows what it broke. The evaluation needs that. If ground truth were
reachable from the diagnostics stack, every number in the report would be worthless.

So: the plant writes a ground-truth run log to a volume the diagnostics stack **never
mounts**. The harness reads it, drives the public API, and scores. Fault injection is a
plant-side operator action; the diagnostics stack cannot trigger a scenario.

Because the simulator clock is dynamic, evaluation questions cannot hardcode timestamps.
The ground-truth log carries absolute times and question templates are parameterised
from it at run time.

### 2.3 Language placement

| Language | Carries |
|---|---|
| **Python** | plant simulation, inspection service, analysis service, agent service, MCP server, harness |
| **C#** | the entire edge gateway — the only component speaking OPC UA on the diagnostics side, and therefore the whole boundary |
| **TypeScript** | two independent frontends in two different stacks, one operator-facing and one analyst-facing |

---

## 3. The plant

### 3.1 The line

Four stations, three buffers, a circulating pool of carriers. **Carriers circulate** —
without that, a worn carrier passes once and the carrier-wear scenario has no
statistical signal to find.

| Station | Function | Signals |
|---|---|---|
| S1 Feeding | separates parts from two feeder lanes onto a carrier | lane fill ×2, takt, part count |
| S2 Joining | presses the parts together | peak joining force, joining distance, takt |
| S3 Inspection | camera captures, vision system classifies | takt; results as events |
| S4 Outfeed | good/bad sorting, returns the carrier | outfeed fill, takt, good/reject counts |

**Every part has an identity, and the identities are asymmetric by design:**

- **Components are individually serialised.** Each feeder lane has a code reader; every
  incoming component carries its own serial and belongs to a supplier lot.
- **The assembly gets its own serial**, created at S1 when the carrier is loaded and
  physically marked at S2 after joining.

Component serials still belong to lots, so lot-level containment survives and exact
as-built genealogy is gained on top. Three records per assembly instead of one —
roughly 32,000 rows for an 18-hour history, which is nothing.

Defaults, all configurable: **18 carriers · 6 s takt · buffer capacity 5**.

**The stations do not share one takt — S3 paces the line.** Inspection is the slowest
operation, so S1 and S2 run slightly faster than it and their buffers fill; S4 matches S3
and its buffer runs near empty. The line's throughput is therefore S3's 6 s, which is the
takt every other number here is quoted against.

This is not decoration. On a perfectly balanced line every buffer oscillates between empty
and one, because each station consumes exactly as fast as the one above produces — and
buffer capacity then bounds nothing, because no buffer ever holds anything. A bottleneck is
what gives a buffer a level to hold, and a level is what makes propagation delayed rather
than immediate.

Buffer capacity is the number that matters — it sets how long propagation takes to become
visible. With B2_3 full at five and a 6 s takt, S3 starves roughly 30 s after S2 stops, and
reasoning about that delay is exactly the analysis's job.

The carrier count has to cover what the filled buffers park, and **12 does not** — measured
in M2a, not estimated. No station holds a carrier between cycles, so every carrier in the
line parks in a buffer, against three buffers of five. At 12 the steady-state margin is one
carrier and takt jitter is what closes it: with jitter off the pool never empties, and at the
configured sigma it pins at zero and S1 suspends for want of a carrier in 29 % of its
suspended cycles — the plant inventing an upstream shortage that no fault caused. 18 clears
it at every jitter setting tested. The number stays provisional until M2a's propagation
measurement confirms it against the real stations rather than a test double.

### 3.2 The clock

```
  start = now − 33 h      catch-up @ 700×  (151–185 s wall)      live @ 1.0×
  ├──────────────────────────────────────────────────────────────►│──────────►
  │     last *completed* night shift lies inside history,         now
  │     whatever hour the boot happens at
```

**Live speed is exactly 1.0.** Anything faster makes simulated time overrun the wall
clock, at which point "now" and "today" stop meaning anything and every time-window
question becomes ambiguous. At 6 s takt a part lands every six seconds, which is
perfectly watchable.

**All timestamps stored as UTC; all shift logic in `Europe/Berlin`.** "Last night" is a
local-time concept and DST is real. Shifts: early 06–14, late 14–22, night 22–06.
"Last night" resolves to the most recent *completed* night shift.

**The 33 h default exists so the last *completed* night shift lies fully inside history at
startup, whatever hour the boot happens at** — otherwise the flagship question has partial
data, which is worse than none because nothing says so.

33 h rather than a rounder number because the requirement is a supremum, not an average.
The worst case is a boot at 05:00 *inside* a running night shift: the last completed one
began the previous evening, 24 h of day-gap earlier, and the autumn fall-back night runs
nine hours. Probing all 366 day-boundaries of a year puts the supremum at
`1 day, 8:59:59.999999`, on 2026-10-25.

Set from measurement, not guessed — see
`docs/superpowers/measurements/2026-09-12-m1-boundary-risks.md`. The earlier 18 h figure was
wrong for the purpose this paragraph states: it holds only for a boot between roughly 06:00
and 16:00. An hourly sweep gives 26 h and is also wrong, because it never lands on the
sawtooth's right edge.

Catch-up duration is `depth / (speed − 1)`, so changing the depth changes the boot time.
At 700× and 33 h it measured **151.3, 160.7 and 184.9 s** over three boots — dominated by
rendering 19,800 images and the inspection round-trips, so it scales with parts per
catch-up rather than with station count. The spec's earlier "≈108 s" assumed 600× at 18 h
with flat-art images and was unreachable once either changed.

**Catch-up happens in-process.** The simulator generates history into its own historian
without touching OPC UA. Nothing crosses the wire at catch-up rates; the gateway pulls it
afterwards via `HistoryRead` at its own pace. See §4.3.

### 3.3 State machines

PackML per ISA-TR88.00.02, using the subset that applies to a continuously running line —
fifteen states, omitting `Completing` and `Complete`, which belong to batch operation.

The semantics do the diagnostic work:

| State | Entered when | Meaning for diagnosis |
|---|---|---|
| **Suspended** | upstream buffer empty (`starved`) or downstream buffer full (`blocked`) | **consequence, by definition** — clears itself |
| **Held** | own condition needing an operator, e.g. a jam | **cause candidate** — requires Unhold |
| **Aborted** | fault shutdown, e.g. joining force out of tolerance | **cause candidate** — requires Clearing then Reset |

Note what this rules out: identifying the root cause as "the first station that raised
its own alarm" would be circular — the simulator produces exactly that and the analysis
would merely find it again — and it does not apply at all to the two scenarios whose
cause lies outside the line, where no alarm is raised anywhere.

Every `Suspended` transition records **which buffer and which direction**. That field is
what makes propagation verifiable rather than inferred, so it is not optional.

We adopt PackML *semantics and structure*, not the OPC UA companion specification
(OPC 30050). Stated openly: an automation engineer recognises the model immediately;
we do not claim conformance.

### 3.4 Inspection

The camera belongs to the station; the vision system is a separate box beside it.

- **The simulator renders the image** — it knows the part's geometry and its defects.
- **The inspection service classifies it** — `inspect(image_bytes, part_context) → InspectionResult`.
- `SimulatedClassifier` resolves the truth through a **side channel** keyed by part id,
  inside `plant-net`, **never through the request**. A real model would ignore that
  channel and the signature would not change.
- It produces plausible per-class confidences plus configurable **false-accept and
  false-reject rates**, so the confidence field carries information.

**The per-class scores are independent, not a distribution.** Each of the six defect classes
carries its own score in [0, 1]; they do not sum to 1, and there is no seventh "good" class.
A good part simply scores low on all six.

This is forced by the scenarios rather than chosen. Scenario 6 is *"optics fouling →
confidence decays across all classes"* — impossible under a softmax, where six values summing
to 1 cannot all fall. Scenarios 4 and 5 need two classes on one part (`misalignment` +
`scratch`, `missing_part` + `contamination`), and pattern DP-02 is keyed on a pair. Mutually
exclusive classes cannot express any of that.

The scalar `Confidence` on the event is confidence in the **OK/NOK verdict**, not in a class.
A good part's verdict confidence is high while all six class scores are low; the earlier
reading — mass spread over six defect classes — reported a good part as 27% confident and ~30%
misaligned, which is the symptom of treating the vector as a distribution.

**The verdict comes from the truth side channel; the confidence scalar is computed from the
image.** `SimulatedClassifier` resolves *what is wrong* by part id as above, but derives
*how sure it is* from a statistic of the rendered image — contrast, local variance. This is
what turns scenario 6 from a stipulation into a cause: fouled optics render a genuinely
degraded image, and confidence falls because the image fell. Stated honestly, a
statistic-to-confidence formula is still a formula, so the stipulation moves one layer down
rather than vanishing — but the fouling now has a real effect on a real measurement, and the
confidence field carries information about the thing it claims to describe.

Defect classes: `gap`, `crack`, `misalignment`, `missing_part`, `scratch`, `contamination`.

**Only rejected parts carry their image** into the OPC UA event. Good parts get a result
without one. This is what real vision systems do, and it is what keeps images inside the
single permitted channel rather than requiring a second one.

### 3.4a Per-part process values

When S2 presses a part, its **force–distance curve** is recorded **against that serial**,
at that moment — with the peak force and the final joining distance as summaries of it —
not reconstructed later by joining the time series on "when was this part at S2".

**The curve rather than the two scalars, because the two scalars cannot separate a press
problem from a material problem.** Two presses reach the same peak at the same final
position by entirely different routes, and the route is the diagnosis: *where* the force
begins to rise is about the incoming components — an undersized component lets the press
travel further before it meets resistance — while *how* the load develops after contact is
about the press. Peak and distance can agree while the curves differ, which is exactly the
case §3.5 scenario 7 turns on. Sampled across the stroke this is a few dozen values per
part, which at this volume is nothing.

Reconstruction is tempting because the data is already there. It is also what real MES
systems deliberately avoid: the association is known exactly at the instant of
production and only approximately afterwards. With buffers, variable takt, micro-stops
and history gaps, "which part was at S2 at 02:14:07" becomes an inference — and inferred
traceability data is what makes a containment list unusable at the moment it matters.

The time series still exists, for trend questions. The per-part record is authoritative
for the part.

### 3.5 Scenarios and the noise floor

A scenario is declarative: a list of `(offset, fault, params)`. Faults available: feeder
starvation, outfeed blockage, joining-force drift, carrier wear, lane contamination,
optics fouling. They fire from the scenario script during catch-up to build history, or by
hand from the HMI while live. **Every injection writes to the ground-truth log.**

| # | Scenario | Expected diagnosis |
|---|---|---|
| 1 | feeder starvation upstream of S1 | external upstream; S2–S4 starve in sequence |
| 2 | outfeed blocked after S4 | external downstream; blockage propagates back to S1 |
| 3 | joining force at S2 drifts down → `gap` rises → alarm → S2 aborts | internal, root S2 |
| 4 | carrier 7 wears → `misalignment` + `scratch` concentrate on it | hypothesis: inspect/remove carrier 7, no stop |
| 5 | feeder lane 2 contaminated → `missing_part` + `contamination` on lane 2 | hypothesis: clean lane 2 |
| 6 | optics fouling → confidence decays across all classes, scrap rate flat | hypothesis: clean the optics |
| 7 | lane 1 receives lot `L-4471` with undersized components → `gap` rises | hypothesis: bad component lot, **plus a containment list** |
| 8 | one single defective component reaches the line | one bad part, explicitly *not* a lot problem |

Scenario 6 is the weakest with a simulated classifier, because the confidence decay is
stipulated rather than emergent. It exercises the right diagnostic path and is the
scenario that improves most the day a real model drops in. Labelled as such.

**Scenario 7 is the strongest test in the set, because the symptom points at the wrong
cause.** Rising `gap` is exactly what pattern DP-01 attributes to "joining process
drifting at S2" — but the joining force is perfectly stable. The only thing separating
them is that the defects correlate with the *lot*, not with the force trend, and that
correlation is invisible without genealogy. So it tests whether the system can resist
the obvious wrong answer when the data supports a better one.

**Scenario 8 is its mirror**, and it exists to stop the system over-generalising: a
single defective component must be reported as a single bad part, not as a lot problem.
Distinguishing "one bad component" from "a bad lot" is a routine and consequential
judgement in real production.

**The line also runs a permanent noise floor** (ambition level: scripted scenarios plus
realistic noise):

- micro-stops — brief jams every ~20 min, randomly placed
- baseline scrap ~1.5 % drawn from a distribution unrelated to any injected fault
- operator interventions — alarms acknowledged after realistic delays, manual restarts,
  the occasional unnecessary reset
- false rejects and false accepts at a configured rate — **owned by the classifier (§3.4)**,
  listed here because they are visible as line behaviour. A real `ModelClassifier` has error
  rates emergently, so modelling them here as well would double-count them the day it is
  swapped in
- genuine carrier-to-carrier variation that is **not** a fault

The last item is the point. Without background variation, finding carrier 7 is a
`GROUP BY`. With it, the analysis needs a significance test and the agent must be able to
say *"that is within normal spread"*.

### 3.6 Ground truth and determinism

Seeded RNG throughout: same seed plus same scenario reproduces the run exactly. The
ground-truth log is JSONL on the gt volume, carrying run id, seed, clock configuration,
every injected fault with simulated timestamps and expected observable consequences, and
the true defect state of every part so false-accept and false-reject rates can be scored.

### 3.7 Plant HMI

Line diagram — four stations, three buffers — colour-coded by PackML state, with the
reason shown on any `Suspended` station. Buffer fill bars. Active alarms with
acknowledge buttons. Clock panel showing simulated time, phase and speed. Fault-injection
panel. A strip of the last parts with thumbnails for rejects.

It is the screen used while building the plant, and the demo console.

---

## 4. The boundary

### 4.1 Address space

```
Objects/
  Line/
    Clock/          SimulatedTime · Phase(booting|catchup|live) · Speed
    Stations/
      S1_Feeding    State · StateReason · TaktTime · LaneFill_1 · LaneFill_2 · PartCount
                    · Lane1_Lot · Lane2_Lot · CurrentAssemblySerial
                    → ComponentReadEvent   (component serial, lane, lot, supplier)
                    → AssemblyCreatedEvent (assembly serial, 2 component serials, carrier)
      S2_Joining    State · StateReason · TaktTime · JoiningForcePeak · JoiningDistance · PartCount
                    → PartProcessedEvent   (assembly serial, force–distance curve,
                                           peak force, distance)
      S3_Inspection State · StateReason · TaktTime · PartCount      → emits inspection events
      S4_Outfeed    State · StateReason · TaktTime · OutfeedFill · GoodCount · RejectCount
                    → PartCompletedEvent   (assembly serial, disposition, reason)
    Buffers/        B1_2 · B2_3 · B3_4
                    each: Level · Capacity · UpstreamStation · DownstreamStation
```

`StateReason` carries `starved`/`blocked` **and the buffer id**.

**What that tree actually counts to: 38 variable nodes.** Ten are static and read on
connect, never historised — each buffer's `Capacity`, `UpstreamStation` and
`DownstreamStation`, plus `Line/Press/StrokeLength`, the millimetre span S2's
force–distance curve is sampled over. That last one is published rather than agreed: only OPC UA crosses
between the stacks, so a private constant in the gateway and another in the analysis service
would be the same number in three places with nothing keeping them equal, and changing the
stroke would silently mis-scale every stored curve with no error anywhere. The axis is not
optional metadata — §3.4a's contact point, which is scenario 7's primary signal, cannot be
computed without it.

It sits under `Line/Press`, a sibling of `Stations`, rather than under S2 — the same
arrangement `Clock` already has, and for a harder reason: topology discovery takes every
variable child of a station as a signal to subscribe to, so a static node placed under S2
would arrive as a twenty-sixth stream. Three restate what an event already carries authoritatively — `Lane1_Lot`,
`Lane2_Lot`, `CurrentAssemblySerial` — and are live-only, because a historised second copy
invites exactly the time-join §3.4a forbids. That leaves **25 historised streams and five
event types**, against M1's two and one. The count is worth stating because all three of
§12's truncation defects scale with it.

Buffer nodes **carry** the stations they sit between, as `String` variables holding the
station's browse name. The gateway reads them on connect and fills the `stations` and
`buffers` tables — **the line's topology is discovered, not configured**.

Variables rather than a custom OPC UA reference type, decided in M2a: the topology is
equally discovered either way, and a custom hierarchical reference type adds
asyncua/UA-.NETStandard interop risk for no diagnostic gain. What matters is that nothing
downstream is *told* the order — S1 is identifiable as the only station that is no buffer's
downstream, and the line walks forward from there. Nothing downstream hardcodes that S2 follows S1. Add a
fifth station and the analysis adapts with no code change; this is also the concrete form
of "point it at a real plant", whose topology likewise comes from its address space.

Exposing the clock is deliberate: the gateway can see that the machine runs on its own
time, in which phase, at what speed. A real plant's clock also differs from yours.

### 4.2 Three kinds of traffic

| What | Mechanism | Why |
|---|---|---|
| states, process values, buffers, counters | variables — subscribed live, historised for backfill | continuous, samplable, deadband applies |
| alarms | events | discrete, with a lifecycle (raise / acknowledge / clear) |
| inspection results | events, one per part | discrete, structured payload, must never be sampled |

**Alarms use a simple custom event type** — code, station, text, severity, active state,
acknowledgement state — not the OPC UA Alarms & Conditions model. Consistent with the
PackML decision: correct semantics, no conformance claim. A&C is a large specification
that buys little the diagnosis needs.

**Timestamps: `SourceTimestamp` = simulated time, `ServerTimestamp` = real wall clock.**
That is exactly what the two fields mean. All analysis uses `SourceTimestamp`, always.
During catch-up the two diverge sharply, which is precisely what happens in a real plant
when a gateway backfills a machine's history.

### 4.3 Startup handshake and backfill

```
connect
  → watch ServerStatus.State until Running          (plant booted)
  → read Clock.Phase                                (history complete?)
  → HistoryRead( last_stored_timestamp → now )      (paged, client-paced)
  → subscribe for live
```

`ServerStatus.State` is a standard OPC UA node with a standard enum — this is the
conventional way a gateway decides a server is alive, not something invented here. PackML
adds a second, machine-level readiness layer: `Stopped → Resetting → Idle → Starting →
Execute` **is** a boot sequence, modelled in the standard.

**Subscriptions are for live data; `HistoryRead` is for the past.** Pushing 18 h of
history through subscriptions would mean ~100 events/second across the boundary.
`HistoryRead` is paged and pull-based, so the gateway sets its own pace. Verified
buildable: asyncua provides `historize_node_data_change()`, `historize_node_event()`,
a `HistorySQLite` backend, and client-side `history_read_raw()` / `read_event_history()`.

The gateway records where its own storage ends, so backfill always closes exactly the gap
it has — after first boot, after a crash, after an outage. **One mechanism, three
situations.** Against a real plant the code path is identical; only the producer of the
history differs.

The UI renders the handshake honestly: *"plant booting — history available from 03:14"*.
The ready signal gates backfill, not the system.

### 4.4 Data quality

| Problem | When | Handled by |
|---|---|---|
| **Overflow** | server dropped notifications during a burst | gateway reads the overflow bit, logs it, writes an explicit gap marker |
| **Duplicates** | backfill window overlaps the live subscription | upsert on `(station, signal, source_timestamp)` |
| **Out of order** | backfill and live arriving concurrently | ordering by `SourceTimestamp` on read, never by arrival |
| **Late data** | local queue flushes after a DB outage | same upsert; nothing downstream assumes arrival order |
| **Gaps** | plant unreachable, or gateway down | gap markers are first-class rows |

Gap markers matter more than they look. Without them, missing data is indistinguishable
from a quiet machine, and the agent will confidently describe a stop that was a blackout.

Note on OPC UA queue semantics: `DiscardOldest=false` replaces the *newest* value, not the
oldest — it is not a lossless setting. Losslessness comes from an adequate queue size plus
a fast publishing interval, with the overflow bit as the honest detector when it fails.

### 4.5 What deliberately does not cross

No writes. No callable methods. No images for good parts. No ground truth. No scenario
control. No second protocol.

### 4.6 Transport security

`SecurityMode Sign` with self-signed application instance certificates and mutual trust
lists. Messages are signed and both sides authenticate each other by certificate. This is
the level real gateways run, and it makes the boundary genuinely authenticated rather than
merely narrow.

**The certificate and the endpoint URL are one puzzle, not two.** An application instance
certificate's subject alternative names must cover the hostname in the endpoint URL, which
under Compose is the service name — so the endpoint URL, the certificate SANs and the
published port have to be solved together. This compounds the `BadTcpEndpointUrlInvalid`
trap (§12) and is why both land in M1.

Trust stores live on volumes so first-connect trust survives restarts. Certificate
generation is a scripted setup step, not a manual one, or `docker compose up` stops being
a single command.

Not in scope: `SignAndEncrypt`, and certificate-based *user* authentication on the OPC UA
session. Documented as the production step.

---

## 5. Inside the diagnostics stack

### 5.1 The gateway (C#)

```
 OPC UA  ──►  local SQLite queue  ──►  batch  ──►  Postgres
             (durable, survives            │         ├── raw landing (verbatim)
              a DB outage)                 │         └── normalised model
                                           │
                                     one transaction — both or neither
```

Raw and normalised are written in a single transaction: one write path, derivation in one
place. The cost is that replaying raw after a normalisation bug means re-running the
gateway's own logic, so it ships with a `--replay-from <timestamp>` mode.

Beyond ingestion it owns: connection lifecycle and reconnect, subscription management,
deadbands, `HistoryRead` backfill, topology discovery, overflow detection, gap markers,
and a `/status` endpoint reporting connection state, last event time, queue depth,
backfill progress and overflow count.

The local queue is a SQLite file on a volume — durable, inspectable, and you can open it
mid-outage to prove it is filling.

### 5.2 Data model

```
raw_events          received_at · source_ts · server_ts · kind · node_id · payload · status_code
                    append-only, verbatim

stations            id · code · name · function · position_in_line     ← discovered
buffers             id · upstream_station · downstream_station · capacity   ← discovered
carriers            id

component_lots      id · lot_code · lane · supplier · loaded_at · depleted_at
components          serial · lot_id · lane · read_at
assemblies          serial · created_at · carrier_id
genealogy           assembly_serial · component_serial · position      as-built structure

part_station_events assembly_serial · station_id · entered_at · left_at · state_at_entry
                    ↑ no source yet — see below
part_process_values assembly_serial · station_id · signal · value      authoritative per part
part_process_curves assembly_serial · station_id · signal · samples[]  §3.4a's force–distance curve
part_dispositions   assembly_serial · at · disposition · reason

signals             source_ts · station_id · signal · value     PK(station, signal, source_ts)
state_changes       source_ts · station_id · from_state · to_state · reason · reason_buffer_id
buffer_levels       source_ts · buffer_id · level
alarms              id · station_id · code · text · severity · raised_at · acked_at · cleared_at
inspection_results  source_ts · assembly_serial · carrier_id · result
                    · defect_classes · confidences · positions · model_version · image_ref
inspection_images   assembly_serial · bytes                      rejects only
ingest_gaps         from_ts · to_ts · reason

sessions            id · subject · created_at            subject = OIDC sub claim
messages            session_id · seq · role · content · created_at
traces              session_id · message_seq · sops_loaded · tool_calls · budget · timings
feedback            session_id · message_seq · useful · matched_reality · comment · created_at
```

**`part_station_events` has no source, and §14 does not need one.** None of §4.1's five event
types carries a station entry or exit instant, so the table exists as a shape with nothing to
fill it — the standing `carriers` had before M2b. Writing the *processing* instant into
`entered_at` would be exactly the quiet wrong answer this system refuses.

§14's "any serial traced end to end: genealogy, station history, disposition" is answerable
without it, and that is the reading this project takes: *station history* means what happened
to this part at each station, which `assemblies.created_at` (S1), `part_process_values` and
`part_process_curves` (S2), `inspection_results` (S3) and `part_dispositions` (S4) already
give per serial. Filling the table would mean a plant change — an event carrying entry and
exit — and buys nothing §14 asks for.

There is no `users` table: identity lives in the provider (§10.5) and sessions carry the
`sub` claim. That is what turns the existing trace tables into an audit trail for free —
and with serial-level quality data and supplier lot codes now in the system, an audit
trail is expected rather than a nice extra.

Note what is deliberately absent: there are no `stop_events` or `defect_patterns` result
tables, because analysis is computed on demand (§5.3), and no `docs` table, because the
knowledge base lives as Markdown in the repository (§6.2).

Plain PostgreSQL. TimescaleDB adds operational complexity that this data volume does not
justify; BRIN indexes on the time columns are sufficient.

### 5.3 Analysis API — which is also the MCP tool set

```
GET  /time/resolve?expression=last+night      → concrete window from the shift calendar
GET  /coverage?from&to                        → what data exists, including gaps
GET  /stops?from&to                           → stop events
GET  /stops/{id}                              → full state timeline + propagation derivation
GET  /alarms?from&to&station
GET  /signals/trend?station&signal&from&to&agg
GET  /inspection/stats?from&to&group_by       → time | carrier | lane | defect_class
GET  /inspection/patterns?from&to             → with significance, not just ranking
GET  /line/status                             → what is happening right now
GET  /knowledge/{id}                          → an SOP or catalogue entry

GET  /parts/{serial}                          → full history, genealogy, process values, image
GET  /parts/affected?from&to&criteria         → containment scoping
GET  /lots/{lot_code}/parts                   → which assemblies contain this lot
GET  /components/{serial}/assembly            → which assembly contains this component
GET  /carriers/{id}/parts                     → which assemblies rode this carrier
```

`/parts/affected` is the query traceability exists for. *"Which parts passed S2 while the
joining force was out of tolerance?"* → 340 serials, 62 rejected, **278 shipped and need
checking.** That is the answer a plant needs at three in the morning, and no amount of
time-series analysis produces it.

`/components/{serial}/assembly` covers single-component recall — a supplier finds a defect
months later and gives you one serial. Real, and only answerable with serialised
components.

Analysis is a **separate deployable service computing on demand**, not a background worker
writing result tables. With a live plant, materialised analysis is permanently stale and
"what is happening right now" becomes structurally unanswerable.

`/time/resolve` exists so that "last night" is resolved by code against a shift calendar,
never guessed by a model. `/coverage` exists so the agent can ask whether it has data
before answering.

**Caching:** results over a *closed* time window can never change, so they cache
indefinitely with no staleness risk. Only the currently-running window needs a short TTL.
This makes concurrent users nearly free.

### 5.4 How a stop is computed

A **line stop** is defined by output, not by state: no part leaves S4 for longer than the
micro-stop threshold of **60 s** (ten missed cycles). Shorter interruptions are recorded
and counted as micro-stops — a rising micro-stop count is its own diagnostic signal — but
are not stop events.

1. Pull every station's state timeline across the window plus a lead-in.
2. Classify each non-`Execute` episode by PackML semantics (§3.3).
3. For each consequence episode, follow its `StateReason` to the named buffer, find when
   that buffer ran empty or full, and find which station's episode explains that. Repeat
   backwards.
4. The chain terminates at a cause candidate, or at the edge of the line.

```
S4 Suspended(starved)  ← B3_4 empty 02:14:32
S3 Suspended(starved)  ← B2_3 empty 02:14:02
S2 Aborted             ← 02:13:40, alarm A-207 "joining force out of tolerance"
                          ROOT · category: internal
```

Categories: `internal`, `external_upstream` (chain ends at S1 starved),
`external_downstream` (ends at S4 blocked), `ambiguous` (two independent cause
candidates — rare at this ambition level, but the field exists rather than silently
picking one).

**The chain is returned as a structured derivation**, not a verdict. The agent may
contradict it (§6.5), which is only possible because the reasoning is visible.

### 5.5 Patterns need statistics, not ranking

For any dimension — carrier, lane, defect class, time bucket — the service returns
observed share, expected share, sample size, effect size and a significance verdict, with
a minimum-sample gate.

Which means `/inspection/patterns` can return **nothing**, and mean it.
*"Carrier 7 is 4 % above average, n=38, not significant"* is a valid and important
answer, and it is what stops the agent inventing a story out of noise.

---

## 6. The agent and the knowledge base

The division of labour agreed for this system:

> **The analysis computes what follows necessarily from the data. The knowledge base
> supplies what requires experience to interpret. The agent applies the second to the
> first — and may contradict the first, with reasons.**

Propagation along the line is bookkeeping over a graph and a timeline, not judgement, so
it stays in code — no SOP repairs an arithmetic error. Everything interpretive lives in
editable documents.

### 6.1 The pipeline

```
question
   │
   ├─ 1  classify             model call, constrained to a fixed set of question types
   │
   ├─ 2  extract time phrase  model reads the language …
   │     resolve window       … code does the calendar maths via /time/resolve
   │
   ├─ 3  coverage check       /coverage — are there gaps in this window?
   │
   ├─ 4  route knowledge      deterministic, from the documents' own front-matter
   │
   ├─ 5  tool loop            budgeted; every call logged
   │
   ├─ 6  verify citations     every cited id resolved against the database
   │
   ├─ 6.5 compose             arrange findings into a readable answer
   │
   └─ 7  deliver              structured answer object
```

Step 2 splits deliberately: the model reads "last night" out of a sentence but does not
compute what it means. Date arithmetic across shift boundaries and DST is what models are
unreliable at and code is exact at.

Step 3 is a guard, not a choice. If the window has gaps, that fact enters the context and
the answer must mention it.

**The fixed set of question types**, which drives both routing (§6.2) and evaluation (§8.1):

| Type | Example |
|---|---|
| `stop_investigation` | "why did the line stand last night?" |
| `quality_investigation` | "why is scrap rising, and where does it come from?" |
| `status` | "what is happening right now?" |
| `trend` | "how has the joining force developed this week?" |
| `statistics` | "show me production and error statistics for the last two hours" |
| `traceability` | "what happened to serial 88431?" · "which parts contain lot L-4471?" · "which parts are affected by the S2 drift?" |
| `knowledge` | "what does *contamination* mean, and what do I check?" |
| `out_of_scope` | anything asking the system to act on the plant, or unrelated to it |

`statistics` is the type that most often produces a chart; the others may attach one.
An unclassifiable question falls back to `knowledge` with a caveat rather than guessing.

### 6.2 Knowledge routes itself

Each document declares where it applies, in its own front-matter. There is no routing
table in code:

```yaml
---
id: SOP-01
title: Investigating a line stop
applies_to:
  question_types: [stop_investigation]
---
```
```yaml
---
id: DP-02
title: Misalignment concentrated on one carrier
applies_to:
  defect_classes: [misalignment, scratch]
  dimensions: [carrier]
---
```
```yaml
---
id: CORE-01
title: How to work
always_load: true
---
```

Adding diagnostic competence means adding a file. That is the project's central claim and
this is the mechanism that makes it literally true.

**`always_load` is the critical flag.** The method never arrives through search — a failed
retrieval must not silently become a failed method. This is the single most common failure
of runbook-driven agents: retrieval misses, the agent improvises, and it sounds exactly as
confident as usual. Free-text search still exists, but only for direct knowledge questions
("what does *contamination* mean?"), never for the procedure.

A retrieval budget caps document count and total size; when routing selects too many,
ranking is by specificity. Context dilution degrades these systems, so the budget is a
correctness measure, not an economy.

```
knowledge/
  core/      always-loaded method and evidence rules          2 files
  sops/      one per investigation type                       4–6
  stations/  what each station does, its failure modes        4
  alarms/    one per alarm code, with documented actions       8–12
  defects/   one per defect class                             6
  patterns/  pattern → hypothesis → checks to run             8–10
```

Roughly 30 small files, hot-reloaded at runtime — an SOP edit must not require a restart,
because that edit is the one made dozens of times while tuning. Reload swaps an immutable
index rather than mutating one in place, so a reload mid-question is safe.

Each document is also an MCP resource: `sop://SOP-01`, `defect://misalignment`,
`pattern://DP-02`.

### 6.3 The answer is an object

The model produces structured findings through a tool call. Nothing is parsed out of prose.

```
findings[]          statement · basis(measured|derived|hypothesis)
                    · citations[] · evidence_strength?
answer_markdown     the composed prose rendering of those findings
method              sops_used[] · tools_called[] · budget_used
caveats[]           coverage gaps · small samples · low confidence
contradiction?      derived_root · agent_root · reasoning
```

`basis` is the claim's epistemic status: `measured` means it is read directly from data,
`derived` means it follows necessarily from data via the propagation computation, and
`hypothesis` means it required interpretation from the knowledge base.

`evidence_strength` is required whenever `basis` is `hypothesis`, and states the support
in figures — for example *"92 % of misalignment defects on carrier 7, n=214, p<0.001"*.
It is a string rendered into the answer, and its presence is checked structurally.

Charts are not a separate field: a chart is a citation kind (§7.3, §7.4) and appears in the
`citations[]` of the finding it supports.

Decomposing into findings makes evaluation **structural rather than judged**: "did it
state an unproven cause as a fact" becomes a field comparison, not a second model's
opinion.

### 6.4 Composition

Step 6.5 exists so structured findings do not read mechanically. The rule that keeps it
safe:

> **The composer arranges; it does not author.**

It decides the headline, ordering, which findings lead and which support, which chart goes
where, what collapses. It may write a summary sentence — but that summary becomes a
finding itself, inheriting the citations of what it summarises, and passes through
verification like everything else. Every sentence the user reads traces to a verified
finding.

### 6.5 Citations, and contradiction

Every cited id is resolved against the database before the answer ships. Cited SOPs are
additionally checked against the set routing actually loaded, so the model cannot invent a
procedure it never read.

**On failure:** the failed ids go back to the model as a tool result — *"stop:19 does not
exist, correct or remove the claim"* — for exactly one retry. If it still fails, the
offending claims are removed and the answer ships with a visible note. The failure is
logged so its frequency is measurable.

**Contradiction is a first-class field.** The agent receiving the propagation derivation
may disagree with it — when an operator intervention mid-stop misleads the mechanical
chain, for instance — and must then populate `contradiction` with its own root and its
reasoning. The UI renders this prominently. Where the mechanics fall short, it is visible
rather than silently wrong.

### 6.6 Answer rules

Answer only from tool results and loaded knowledge · name cause and consequence separately
· anything derived from a pattern is a hypothesis with its evidence strength attached ·
cite everything · state what is missing rather than filling it · recommend only documented
actions · if you disagree with the computed propagation, say so and show your reasoning.

### 6.7 Clarifying questions

> **Ask back only when the plausible readings lead to materially different investigations
> *and* no reading is clearly more likely.** Otherwise assume the most likely one and say
> so in a caveat.

| Situation | Behaviour |
|---|---|
| no time expression at all | assume most recent stop / current shift, state it |
| several stops in the window, question is singular | take the longest, state it, list the others |
| refers to something that does not exist | correct it and name what does — that is answering, not asking |
| could be about downtime *or* scrap, and the answers differ | **ask** |
| window has no data at all | offer the nearest window that does |

### 6.8 Failure behaviour

Tool errors return to the model as tool results rather than crashing the request; after
several consecutive failures the run aborts with an honest message. Budget exhaustion
produces a **partial answer stating what it could not finish** — never a silently
truncated one.

### 6.9 Model provider layer

```
          pipeline  ──►  canonical: system · messages · tools · structured output · stream
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
              Anthropic            OpenAI-compatible
                                         │
                             ┌───────────┼───────────┐
                             ▼           ▼           ▼
                          OpenAI      Ollama       vLLM
```

Two adapters, three deployment targets: OpenAI-compatible is the de-facto interface for
local serving too, so one adapter covers a hosted alternative *and* the local-model path
without a third implementation.

The canonical surface is small — system prompt, message history, tool definitions, tool
results, structured final output, streaming deltas. The two seams that leak and need
design rather than papering over: tool-call representation differs between providers, and
structured output is guaranteed differently (schema-constrained tool call versus a
JSON-schema response format).

A third-party shim (LiteLLM) was considered and rejected: the needed surface is a few
hundred lines, and the shim's abstraction is unlikely to match the structured-output
guarantees this pipeline depends on. Owning the thin layer beneath a deliberately
hand-built tool loop is the coherent choice.

Default model: `claude-sonnet-5`.

### 6.10 Concurrent use

Multiple users, no accounts, one plant, shared data — no multi-tenancy required.

- **Sessions live in Postgres**, not memory. The tool-call log needed for traceability is
  per-conversation anyway, so sessions, refresh-safe transcripts and the audit trail come
  from one decision. Session ids are unguessable.
- **A concurrency cap toward the model API** — one queue, a small worker pool, plus the
  per-session iteration budget.
- **Closed-window caching** (§5.3) makes concurrent analysis load nearly free.
- **Streaming progress** — *reading stop events… consulting SOP-01… checking joining
  force*. Better under concurrency, and the best demo asset in the project: it makes the
  reasoning visible instead of asking anyone to trust it.

### 6.11 MCP server

Streamable HTTP transport, bound to localhost, inside `diag-net`, never on `field-net`.
stdio is unsuitable because it requires the client to spawn the server as a subprocess,
which does not work across a container boundary. Pin a spec revision explicitly: the
2026-07-28 revision reworked Streamable HTTP substantially (removing protocol-level
sessions, the standalone GET stream and `Last-Event-ID` resumption).

| Primitive | Content |
|---|---|
| **tools** | the analysis queries — read-only, every one |
| **resources** | the knowledge base, by URI |
| **`diagnose(question)`** | the full staged pipeline, verified citations included |

Exposing resources as well as tools is what makes this more than a data API: an external
agent can read `sop://SOP-01`, follow it, call the same tools and reach the same
conclusion. **The method transfers to an agent we did not build** — which is the project's
central claim, demonstrated rather than asserted.

**One contract, two bindings.** The OpenAPI spec defines the capabilities; the frontend
binds over REST, agent runtimes bind over MCP, both generated from one source. A test
asserts that the MCP tool list and the OpenAPI operation set derive from the same
definition. Neither binding can do anything the other cannot.

There is no plant-side MCP: operating the plant agentically would let the diagnostics side
know what it broke, destroying the evaluation.

---

## 7. Frontends

Two applications in two stacks, sharing nothing but a small set of design tokens. They are
on opposite sides of the boundary and must not develop a shared dependency across it.
Both React + Vite.

### 7.1 Plant HMI

See §3.7. Machine only — no chat, no diagnostics. Putting a chat here would require a
second channel from `plant-net` into the diagnostics stack, which destroys the one claim
the two-stack split exists to make.

### 7.2 Diagnostics UI

One responsive application. Every user is a browser client, whether that browser is on a
tablet at the line or on a desk.

- **Chat with streaming progress**
- **Evidence panel** bound to the answer's citations; clicking one opens the underlying
  data, including the SOP text itself — a citation you cannot open is barely a citation
- **Stop timeline** — station states as a Gantt across the window, propagation chain drawn on it
- **Charts** (§7.4)
- **Part detail** — genealogy tree, station-by-station timeline, the process values
  recorded for *that* part, inspection result and image
- **Serial search** and a **containment view** — the affected set with an export
- **Plant status banner** from the gateway — *connected · backfilling · plant offline, history to 03:14*
- **Contradiction banner** when the agent disagrees with the computed propagation
- **Reasoning trace**, permanent and collapsible under every answer — SOPs loaded, every
  tool call with arguments and timings, budget consumed. It is stored for the audit trail
  anyway, so surfacing it is nearly free, and it makes the "did it follow the method"
  axis verifiable by eye.
- **Feedback** — *was this useful?* and *did this match what you actually found?*
  The second is the operator's own ground truth and the more valuable signal.

### 7.3 The evidence contract

Citations are typed objects, each resolvable to a real endpoint:

```
{ kind: "stop",    id: 12 }                                   → GET /stops/12
{ kind: "alarm",   id: 207 }                                  → GET /alarms/207
{ kind: "part",    id: 88431 }                                → GET /parts/88431
{ kind: "pattern", dimension: "carrier", key: "7", window }   → GET /inspection/patterns
{ kind: "signal",  station: "S2", signal: "joining_force", window }
{ kind: "sop",     id: "SOP-01" }                             → GET /knowledge/SOP-01
{ kind: "serial",  value: "A-88431" }                         → GET /parts/A-88431
{ kind: "lot",     lot_code: "L-4471" }                       → GET /lots/L-4471/parts
{ kind: "containment", query, count, serials[] }              → GET /parts/affected
{ kind: "chart",   chart_type, source: <tool_call_id>, options }
```

Each kind has a renderer; adding a citation type means adding a renderer, nothing more.
Because the contract lives in OpenAPI, **TypeScript types are generated from it** — the
frontend cannot drift from the API.

### 7.4 Charts

A chart is a citation, not a decoration beside one.

**The data always comes from a verified tool result.** The specification *references* a
tool call by id; it never carries values the model typed. This kills the classic failure
where a model draws a confident chart from invented figures — and a wrong chart reads far
more authoritatively than a wrong sentence.

**Menu first, free description as fallback.** A fixed vocabulary covers everyday questions;
anything that fits none of them falls through to a declarative free-form specification
(Vega-Lite), which is data rather than code and therefore safe.

The built-in vocabulary:

| Type | Use |
|---|---|
| timeseries with event bands | a process value with stop and alarm periods shaded |
| state Gantt | station states across a window |
| Pareto | defect classes by frequency |
| stacked bar by dimension | defects by carrier or by lane |
| rate over time | scrap rate, micro-stop rate |
| summary tiles | counts, rates, availability figures |

The fallback path is the least-exercised code in the frontend precisely because it fires
rarely; it needs its own tests rather than relying on incidental use.

---

## 8. Evaluation

The harness runs outside both stacks, reads the ground-truth log from the plant's volume,
and drives the **public** contract — the same API a user or an external agent would use.

### 8.1 Case classes

The eight scenarios are the easy part. These decide whether it is a system:

| Class | What it checks |
|---|---|
| **Root cause** | correct root station, correct category, consequences *not* named as cause |
| **Negative** | "was there a pattern on carrier 3?" — there was not. Must say so, must not invent one |
| **Coverage** | window containing an ingest gap → must state the data is incomplete |
| **Hypothesis labelling** | carrier wear → `basis: hypothesis` with evidence strength, never `measured` |
| **Micro-stop** | asked about a 12-second interruption → "that was a micro-stop, not a line stop" |
| **Ambiguity** | genuinely ambiguous → asks. *And the mirror:* looks ambiguous, is not → asking is a failure |
| **Contradiction** | operator intervention mid-stop misleads the mechanical chain → agent flags it |
| **Out of scope** | "change the joining force" → declines; it is a read-only system |
| **Traceability** | "what happened to serial 88431?" → correct genealogy, correct station history, correct disposition |
| **Containment** | exactly the affected serials — no misses, no false inclusions. Deterministically scorable against ground truth |
| **Lot vs. single part** | scenario 7 vs. scenario 8 — a lot problem must not be reported as one bad part, nor the reverse |
| **Misleading symptom** | scenario 7: rising `gap` with a stable joining force must resolve to the lot, not to DP-01's drift hypothesis |
| **Statistics and charts** | "production and error statistics for the last two hours" → a chart of a fitting type, bound to a tool result, with no model-authored numbers |
| **Citation integrity** | automatic on every case: every cited id resolves |

Roughly 25 cases. Questions are parameterised from the ground-truth log at run time,
since the clock is dynamic and timestamps cannot be hardcoded.

### 8.2 Two axes

1. **Conclusion** — did it reach the right answer?
2. **Method** — did it follow the prescribed procedure? Checked against `method.sops_used`
   and the tool-call sequence.

The second axis exists because an agent that is accidentally right should not score the
same as one that reasoned correctly.

### 8.3 Reporting

**Rates, not verdicts.** The plant is deterministic given a seed; the model is not. Each
case runs three times and the report shows pass rates per case and per class, with no
arbitrary threshold declaring the system "good". A single green run proves nothing, and
reporting one as pass/fail would be the only dishonest number in an otherwise honest
project.

Output: a JSON artifact per run, plus a table in the README carrying seed, model, date and
rates per class.

### 8.4 The analysis has its own tests, with no model involved

Propagation correctness is where the system's truth actually lives, and it is directly
checkable: inject a known fault, assert the computed chain against ground truth. That
suite runs in seconds, costs nothing, has no variance, and catches the failures that matter
most. It is **more important than the LLM evaluation**, and it is the suite most easily
forgotten in a project whose headline feature is an agent.

---

## 9. The improvement loop

Not self-learning — nothing learns weights. The precise version is stronger:

> **Feedback and evaluation failures accumulate → the system analyses where it went wrong
> → it proposes a concrete diff to the knowledge base → the harness runs before and after
> → a human approves or rejects.**

It fits this architecture because of three earlier decisions, not by coincidence:

- **Knowledge is files**, so a proposal is a reviewable Git diff, not an opaque adjustment
- **Every session stores its full trace**, so failure analysis has real material
- **The harness already exists**, so a proposal arrives *with evidence that it helps*:
  "these 4 cases were failing, this SOP change fixes 3, here is the before and after"

That last point is what makes it serious. A system that proposes changes is common; one
that proves them against a fixed test set before a human sees them is not.

**Constraints, stated up front:**

- **It never applies anything.** Human approval is always the gate — which is precisely
  why file-based knowledge was the right substrate.
- **It runs primarily on evaluation failures**, which the harness produces in quantity with
  ground truth attached. User feedback is a second input demonstrated on a handful of real
  sessions, not claimed at scale — this project will not have a week of real users, and a
  loop that depends on them would be theatre.
- **Implicit signals** are collected where free: a user re-asking the same thing within a
  minute, or never opening the evidence panel.

---

## 10. Non-functionals

### 10.1 Repository layout

```
machine-agent/
  plant/          compose.yml · simulator·PY · inspection·PY · hmi·TS · scenarios/
  diagnostics/    compose.yml · gateway·C# · analysis·PY · agent·PY · mcp·PY · ui·TS
  knowledge/      core/ sops/ stations/ alarms/ defects/ patterns/    ← top level, deliberately
  harness/        cases/ · runner·PY
  contracts/      analysis.openapi.yaml · answer.schema.json
  docs/specs/
```

One repository, two independent stacks — they share no code and no runtime path, only a
Git history. `knowledge/` sits at the top level rather than inside the agent service
because it is what a domain expert edits and its prominence should match its importance.
`contracts/` is the single source of truth: the analysis API, the MCP tool definitions and
the generated TypeScript types all derive from it.

### 10.2 Testing

| Level | What | Cost |
|---|---|---|
| **Unit** | PackML transitions · propagation · significance tests · time resolution · citation verification | free, fast |
| **Contract** | OpenAPI validated both directions; generated TS types must compile | free, fast |
| **Integration** | gateway against a real asyncua server in a container — ingest, outage, backfill | free, slower |
| **Authenticity** | the proofs from §1, each as an executable test | free, slower |
| **Evaluation** | the harness — model-dependent, reported as rates | costs money, varies |

Only the last level costs money and varies. Correctness lives in the levels that do not.

### 10.3 Configuration and secrets

Each stack has its own `.env`; the model API key exists only in the diagnostics stack.
Everything with a number in it — takt, catch-up speed, history depth, buffer capacity,
micro-stop threshold, deadbands, significance gates, budgets, seed — is configuration, not
a constant buried in code. No secrets in the repository.

### 10.4 Observability

Structured JSON logs everywhere, with a correlation id threading a question through
agent → analysis → database. The gateway exposes `/status`. The agent's trace is persisted
for the UI and doubles as the observability story. No Prometheus; log-based is sufficient
at this size.

### 10.5 Identity and access

**One issuer, brokered.** The application is an OIDC client of exactly one issuer —
**Zitadel**, running in the diagnostics stack and reusing the existing Postgres. That
issuer can have Google and Microsoft Entra configured as *upstream* providers, in which
case a user is bounced there and returns, and Zitadel issues its own token to the
application.

```
   diagnostics stack  ──── OIDC ────►  Zitadel  ──┬──► Google          (not configured)
   (knows ONE issuer,                   (broker)  ├──► Microsoft Entra (not configured)
    one claim shape,                              └──► local users     (default)
    one role source)
```

This is the decision that makes SSO cheap later: adding a provider is an admin-UI task
with a client id and secret, no code and no redeploy, because the application never
learns that Google exists. The alternative — validating tokens from several issuers
directly — means multiple JWKS endpoints, differing claim shapes, and nowhere to store
roles, which ends in rebuilding the broker badly.

**Roles as token claims: `admin` and `user`.** No permission matrix, no groups, no
per-resource rules.

| | user | admin |
|---|---|---|
| ask questions, view answers, evidence, traces | ✓ | ✓ |
| traceability and containment queries | ✓ | ✓ |
| raw model exchange and system prompts | | ✓ |
| reload the knowledge base | | ✓ |
| approve improvement-loop proposals | | ✓ |
| manage users | | ✓ |

**Roles live in the issuer, never upstream.** Google and Microsoft will never state that
someone administers your plant, so federated users default to `user` and are promoted by
hand. Account linking is by *verified* email or explicit action only — never silently on
an unverified claim.

**What is deliberately not built:** no self-registration, no password reset, no email
verification, no user table of our own. Users are created in the issuer's admin UI. The
frontend uses `oidc-client-ts` with authorisation code + PKCE; the services validate the
JWT against the issuer's JWKS, check `aud`, and read the role claim — one shared module of
roughly thirty lines.

**The MCP server** validates the same tokens and checks the audience. The full OAuth 2.1
resource-server flow that the MCP specification describes — Resource Indicators per
RFC 8707, protected-resource metadata per RFC 9728 — is documented as the production step
rather than built here.

**The plant stack authenticates separately and must never share an issuer** with the
diagnostics stack: a shared token issuer or user store would be a second channel across
the boundary, which is exactly what the two-stack split exists to prevent. The plant HMI
has one privileged action — fault injection — gated by a shared token in the plant's
`.env` and checked by the simulator. Honest for a machine-local HMI, and it keeps the two
identity systems disjoint.

**The OPC UA boundary** runs `SecurityMode Sign` with mutual certificate trust (§4.6).

**Remaining honest limits:** every HTTP service binds to localhost; there is no TLS
between containers; `SignAndEncrypt` and OPC UA user authentication are out of scope. This
is a local demonstration system and the README says so in those words.

### 10.6 Determinism

Seed per run, recorded in the ground-truth log. The plant is fully reproducible. The model
is not, which is why evaluation reports rates.

### 10.7 Toolchain and container conventions

**Python: `uv`, everywhere, for both the interpreter and the dependencies.** It replaces
pyenv, pip and venv with one tool, and it is what makes a container build reproducible
rather than merely repeatable.

- The Python version is pinned in `.python-version` and `requires-python`, and **uv
  provisions the interpreter itself** — the base image does not dictate it. Default 3.13;
  confirm `asyncua` supports it during M1 rather than assuming.
- `uv.lock` is committed. Builds run **`uv sync --locked`**, which *fails* if the lockfile
  would change. `--frozen` uses whatever is present without checking, so it belongs only in
  the dependency-only Docker layer where the lockfile has been bind-mounted deliberately.
- The uv binary is copied from its official pinned image, never fetched by a script at
  build time: `COPY --from=ghcr.io/astral-sh/uv:<pinned> /uv /uvx /bin/`.
- Dockerfiles are layered for caching: copy `pyproject.toml` and `uv.lock`, run
  `uv sync --frozen --no-install-workspace`, *then* copy source and `uv sync --locked`.
  `--no-install-workspace` (not `--no-install-project`) skips every workspace member, so the
  cached layer survives an edit to any of them. Container env: `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`.
- Multi-stage: build with uv, run from a slim image carrying only the resolved
  environment.

**Two workspaces, one per stack — not one at the repository root.** `plant/` and
`diagnostics/` each own a uv workspace with their services as members. §10.1 claims the
two stacks share no code; a single root lockfile would make that false at build time, and
it would mean bumping a dependency in the agent service could move a version underneath
the simulator. The duplication between the two lockfiles is the correct cost — the plant
has no business caring what the diagnostics stack resolves. `harness/` is a third,
independent project: it is the referee and belongs to neither.

**The same discipline for the other two languages**, so the convention is uniform rather
than a Python exception:

| | Pinned by | Locked by |
|---|---|---|
| Python | `.python-version`, `requires-python` | `uv.lock`, `uv sync --locked` |
| .NET | `global.json` with `rollForward: disable`, `Directory.Packages.props` | `packages.lock.json`, restore with `--locked-mode` |
| Node | `.nvmrc`, `engines`, pnpm via corepack | `pnpm-lock.yaml`, `--frozen-lockfile` |

Every base image is pinned by digest, not by tag.

Three .NET details that stop being optional once lock files are in play. `global.json` needs
`"rollForward": "disable"` — SDK-implicit `PackageReference` items change between SDK
versions, and a different SDK on another machine is the most common cause of a locked-mode
restore that fails in CI but not locally. **Central Package Management**
(`Directory.Packages.props`) and the lock file are complements, not alternatives: CPM records
which version was asked for, the lock file records what restore actually resolved. And a
repo-local `NuGet.config` must carry `<clear />` in both `packageSources` and
`packageSourceMapping` — without it a machine- or user-level source silently joins the
resolution set, which is a dependency-confusion hole.

### 10.8 Quality gates

Tests (§10.2) establish that the system does the right thing. These establish that the code
is fit to be worked on — which matters more here than usual, because eight milestones across
three languages is long enough for entropy to win if nothing pushes back.

**`make check` is the single entry point**, green before every commit and identical to what
CI runs. `make fmt` formats in place, `make lint` checks without changing, `make test` runs
the suite, `make check` is lint plus test.

| | Format & lint | Types | Tests |
|---|---|---|---|
| Python | `ruff format`, `ruff check` | `mypy --strict` | `pytest` |
| C# | `dotnet format --verify-no-changes` | `Nullable=enable`, `TreatWarningsAsErrors=true`, `AnalysisMode=All` | `xunit` |
| TypeScript | `oxlint check` | `tsc --noEmit`, `strict: true` | `vitest` |

One tool per job per language, chosen for speed and for having no configuration argument:
`ruff` replaces black, isort and flake8; `oxlint` replaces eslint and prettier. The C# column
costs nothing at all — it is three properties in the csproj, and nullable reference types
plus warnings-as-errors is the highest-value quality lever .NET offers.

Rules:

- **Warnings are errors.** A gate that can be ignored is not a gate.
- **No blanket suppressions.** `# type: ignore`, `# noqa`, `oxlint-ignore` and
  `#pragma warning disable` each require a specific rule code and a comment giving the
  reason. Never file-wide, never bare.
- **New code is typed.** No untyped signatures in Python, no `any` in TypeScript, nullable
  reference types enabled in C#.
- Formatting is never a review topic — the formatter decides.

**Enforcement in three layers.** A pre-commit hook runs the fast subset (format and lint) so
mistakes surface in seconds. `make check` is the full gate, and it is what a commit asserts
passed. CI runs the identical target once a remote exists. `--no-verify` is never used.

The commit convention follows from this: **a commit is evidence the gates passed** — the same
principle as the authenticity proofs in §1, where the artifact carries its own proof.
Conventional-commit prefixes, one commit per completed plan step, never one per file and
never one per session.

Frontend tooling is revisitable at M6, when the UI stops being a chat box and React-specific
lint rules may argue for eslint over oxlint.

---

## 11. Technology choices

| Component | Technology |
|---|---|
| Simulator | Python, `asyncua` |
| Inspection service | Python |
| Plant HMI | TypeScript, React + Vite |
| Edge gateway | C#/.NET, OPC Foundation UA-.NETStandard |
| Database | PostgreSQL (plain, with BRIN indexes on time columns) |
| Analysis service | Python, FastAPI |
| Agent service | Python, FastAPI |
| MCP server | Python, Streamable HTTP |
| Identity provider | Zitadel (OIDC, brokering upstream), reusing the same Postgres |
| Diagnostics UI | TypeScript, React + Vite |
| Harness | Python |
| Python toolchain | `uv` — interpreter and dependencies, one workspace per stack (§10.7) |
| Deployment | Docker Compose ×2, external `field-net` |

**Why this split.** OPC UA's reference implementation lives in .NET and the edge gateway is
the archetypal long-running, narrowly-scoped .NET service — that is the real industrial
division of labour, not a training exercise. Python owns simulation, analysis and the LLM
work, where its ecosystem is strongest and where the logic changes most often. TypeScript
owns two independent user-facing surfaces on opposite sides of the boundary.

**Rejected alternatives.**

- *Everything in Python* — fastest to build, but the gateway is where the boundary
  discipline lives and .NET is where that discipline is native.
- *Everything in C#* — markedly slower for analysis and model integration.
- *Go, Rust or Node for the gateway* — no advantage over C# here.
- *TimescaleDB* — operational complexity unjustified at this data volume.
- *A vector database* — ~30 documents with deterministic routing; embeddings would add a
  component and remove determinism.
- *An agent framework* — the tool loop is the thing being built; a framework would hide it.
- *LiteLLM as provider shim* — see §6.9.
- *Keycloak as the identity provider* — the deepest protocol support and the most
  documented path for Entra federation, but it idles above 1 GB, wants 4 GB to be
  comfortable, and slows every `compose up`. That is daily friction paid for a feature
  not needed yet; brokering keeps it a ten-minute change whenever it is.
- *Local authentication with an OIDC-shaped interface* — sounds simpler and is not: a
  login endpoint, token issuing, refresh, logout, password handling and a
  user-management UI, all self-written, in the one domain nobody should own.
- *Batch-level component tracking only* — cheaper, but loses exact as-built genealogy and
  cannot answer single-component recall. Component serials still carry their lot, so
  serialising costs modelling effort without costing lot-level containment.
- *Deriving per-part process values from the time series* — see §3.4a.
- *OPC UA Alarms & Conditions* — see §4.2.
- *The OPC UA PackML companion specification* — see §3.3.

---

## 12. Risks

**M1 measured the first five. Outcomes below; the full report with every number is
`docs/superpowers/measurements/2026-09-12-m1-boundary-risks.md`.**

| Risk | Impact | Mitigation | M1 outcome |
|---|---|---|---|
| asyncua history backend performance with ~20k rows per stream | backfill slow or unreliable | measured in M1 before anything depends on it | **the risk was misplaced.** Latency is a non-issue: 19,800 rows per stream backfilled in 60 s against a 300 s budget, p99 1.3 s, sublinear. The danger is silent miscounting — see the three truncation rows below. `pg_rows == read_rows` exactly, zero gaps |
| UA-.NETStandard `HistoryRead` client ergonomics | gateway work larger than estimated | measured in M1 | **confirmed larger.** 1.5.378.176 ships no `HistoryRead` helper; `HistoryClient` is in the unreleased 2.0 line. Paging, decoding, continuation-point release and per-stream page sizing are all ours — ~250 lines |
| OPC UA endpoint URL vs. Docker hostname (`BadTcpEndpointUrlInvalid`) | `docker compose up` does not just work | same port inside and out; endpoint URL configured to the service name; proven in M1 | **PASS.** All four client positions connect at `Sign`, including UA-.NETStandard inside `field-net` over the service name with `checkDomain` on. No manual host configuration: `localhost` works because the certificate's IP SAN covers it |
| Structured events carrying image bytes | rejects arrive without evidence | exercised in M1 with one station | **PASS, 1,005× headroom.** But the limit that fires is not the one assumed: `MaxByteStringLength` and `MaxMessageSize` are unenforced in asyncua 2.0.1, and the ceiling comes from the chunk-count limit. The binding end-to-end constraint is the .NET client's 4 MiB, 38× above `img_p99` |
| OPC UA certificate SANs vs. Docker service names | signed connections fail in a way that looks like a network problem | certificate generation scripted against the same names as the endpoint URL; solved together with the endpoint trap in M1 | **PASS.** The subject must be exactly three RDNs — a fourth fails `Utils.CompareDistinguishedName` on field count and presents as "no usable certificate" while the PKI is fine |
| **asyncua silently truncates reads at 10,000 values** | a backfill returns 10,000 of 19,800 and reports success — no exception, no bad `StatusCode` | bounded windows with a reconciled count per window; a window returning the ceiling is refused rather than trusted | **found in M1.** M2 multiplies the signal count by roughly ten, so this compounds |
| **asyncua silently truncates event-history paging** | a client paging smaller than the server's cap receives one page and no continuation point, and stops early believing the window complete | treat a full final page with no continuation point as truncated and halve the window until every part comes back short | **found in M1: 96% of event history lost on a green run.** `read_node_history` has the identical structure, so variables are only safe when the page size happens to equal the server's cap |
| **asyncua's write path discards the oldest** | the internal subscription queue caps each monitored item at 10,000 and drops the oldest, destroying the earliest history as it is generated | pace generation against the queue, and assert the historian's contents rather than the generator's ledger | **found in M1: the first 16 h 20 min of a 33 h run destroyed** — precisely the shift the history depth exists to guarantee |
| **asyncua's historian cannot read back the timestamp it wrote** | one row poisons every `HistoryRead` whose window contains it, permanently — the backfill fails rather than returning short, so no data is lost, but the run cannot complete | fix it on the **read**: register a converter that accepts both spellings before the historian is used | **found in M2a.** `HistorySQLite` stores its timestamps via `isoformat()`, which omits microseconds when they are exactly zero; sqlite3's own `TIMESTAMP` converter then fails on what its adapter wrote (`ValueError: b'58+00'`). Measured at one row in 386,782, so `P(clean 33 h run) ≈ 0.68` — roughly one authenticity run in three dies. **It affects `SourceTimestamp` as well as `ServerTimestamp`**, and that is what decides the fix: `SourceTimestamp` is simulated time, written by the plant, so there is no wall-clock jitter to lean on and no value we may quietly perturb — the one column all analysis reads is the one that cannot be worked around on the write side |
| **the buffer `Level` streams truncate where M1's signals did not** | 16.7 % of every window lost, silently, on a green run | the same full-page-with-no-continuation-point guard M1 built for events, shared by the variable path | **found in M2a, and it is §12's compounding prediction coming true.** The server returned no continuation point in 1,980 windows. `Level` steps by one at ~1,200/h against a 1,000-row page, so a one-hour window returns exactly 1,000 and stops. M1's signals were too sparse to reach the page size; M2a's are not |
| OIDC in a browser SPA — redirect URIs, token refresh, silent renewal | login works locally and breaks on any change of host or port | `oidc-client-ts` rather than hand-rolled; redirect URIs derived from one configured origin | M5 |
| Scope | eight milestones is weeks of work | milestone boundaries are releasable; M4 is the first honest demo over API and MCP | open |
| Knowledge tuning without measurement | SOP edits become guesswork | the harness lands at M6; before/after diffing from then on | M6 |

The first five all live at the boundary, which is why M1 exists and why it is thin
everywhere except there. The three that follow them were found by building it: all three
are ways this stack discards data and reports success, and all three are detectable from
the client side — which is why the guards live in the gateway rather than in a request that
the server behave. That distinction is what makes "point it at a real plant" a claim rather
than a hope.

---

## 13. Milestones

```
M1  WALKING SKELETON        one station · two signals · one event with an image
    the whole chain, thin   OPC UA across two stacks · gateway with queue + backfill
                            postgres raw+clean · one analysis endpoint · one tool
                            a chat box that answers one question with one real citation
                            ─── answers: does the boundary actually work? ───

M2  THE PLANT IS REAL       three sequenced plans. Each is releasable, and each carries
                            its own gateway subscriptions and its own migration, so no
                            milestone ends with streams that nothing reads
    M2a the line runs       4 stations · PackML · buffers · carriers · clock and catch-up
    M2b every part          component and assembly serials · lots · genealogy
        has a name          per-part process values · force–distance curves
    M2c the line            scenarios 1–8 · noise floor · ground-truth log · alarms
        misbehaves on
        purpose             ─── plant HMI grows across all three ───

M3  THE ANALYSIS IS REAL    propagation with derivation · significance · coverage
                            time resolution · traceability and containment queries
                            full OpenAPI contract · unit tests

M4  THE AGENT IS REAL       staged pipeline · knowledge base · routing · verification
                            composer · provider layer · traceability SOPs · MCP server
                            ─── first honest demo, over API and MCP ───

M5  SECURITY AND IDENTITY   Zitadel · roles as claims · JWT validation module
                            MCP token validation · plant HMI fault-injection gate
                            ─── before the UI, so nothing is retrofitted ───

M6  THE UI IS REAL          login · evidence panel · charts · timeline · trace
                            SOP viewer · serial search · genealogy · containment view

M7  THE REFEREE             harness · ~25 cases across all classes · rates

M8  THE LOOP                improvement proposals from evaluation failures
```

**The ordering principle: the first milestone hits the riskiest path, not the easiest
one.** M1 is thin everywhere, but nothing in it is faked — that is what makes it worth
doing first. Every later milestone is a self-contained addition rather than a rewrite.

Security lands at **M5, before the UI** — deliberately. Building the UI against
unauthenticated APIs and retrofitting login afterwards is how auth ends up bolted on.
OPC UA transport signing is the exception: it belongs to M1, because it is boundary-setup
work and interacts with the endpoint-URL problem M1 exists to solve.

Target: all of M1–M8.

---

## 14. Definition of done

- `docker compose up` on each stack, no manual steps, no fixing hostnames by hand
- The plant boots, builds its own history, signals ready, and runs live at 1:1
- The diagnostics stack starts and answers questions with the plant stack shut down
- Every authenticity proof in §1 exists as a passing test
- The OPC UA connection is signed, with mutual certificate trust, and survives a restart
  without re-trusting by hand
- All eight scenarios produce correct root cause or correctly-labelled hypothesis, with
  citations that resolve
- Scenario 7 resolves to the component lot rather than to the joining process, and
  produces a containment list scored exactly against ground truth
- Any serial can be traced end to end: genealogy, station history, the process values
  recorded for that part, inspection result, disposition
- Unauthenticated requests to every diagnostics endpoint return 401, MCP included; an
  admin-only action performed as `user` returns 403
- The system says "no significant pattern" when there is none, and "no data" when there
  is none
- Asking for statistics over a time window produces a chart whose data came from a tool
  result, never from numbers the model wrote
- An external MCP client reaches the same tools, reads the same SOPs, and reaches the
  same conclusion
- The harness runs unattended and produces rates per class
- The improvement loop proposes at least one knowledge diff with before/after evidence
- Adding Google or Microsoft as a login option is demonstrably an admin-UI task: no code
  change, no redeploy
- README covers architecture, decisions with rejected alternatives, known limits, the
  security posture in plain words, and an open account of the AI-assisted development
  process

---

## 15. Open decisions

- Chart library for the fixed vocabulary (Recharts / visx / ECharts) — deferred to M5
- Significance test choice (binomial exact vs. two-proportion z) and the minimum-sample
  gate — deferred to M3, needs the noise floor's real distribution
- ~~Exact catch-up speed, history depth and takt — set from the M1 measurement, not
  guessed~~ **Closed by M1.** History depth **33 h**, catch-up **700×**, takt **6 s**
  unchanged. Measured in
  `docs/superpowers/measurements/2026-09-12-m1-boundary-risks.md`; §3.2 carries the numbers
  and the reasoning.
- ~~**Python 3.13 or 3.14** — revisit once M1 confirms `asyncua` support~~ **Answered by M1,
  and 3.13 stands.** `asyncua` 2.0.1 runs on 3.13 and every defect M1 found in it (§12's three
  truncation rows) is a logic defect that a newer interpreter would not touch, so there is
  nothing here pulling towards 3.14. What would move it is a dependency that requires it;
  revisit at M2, when the plant grows twenty-three more historised signal streams and four
  more event types, and the dependency set changes. M2's own known additions (`fastapi`,
  `uvicorn` for the plant HMI) are already resolved in the workspace and do not move this.
- ~~Whether scenario 6 (optics fouling) survives review once confidence decay is visible in
  practice, given that it is stipulated rather than emergent~~ **Closed at M2, by removing
  the stipulation.** The simulator renders genuinely degraded images and the classifier
  derives its confidence from the image (§3.4); the verdict still comes from the truth side
  channel. The scenario keeps its label as the one that improves most when a real model
  arrives
- MCP specification revision to pin — decide at M4 against what clients actually support

**Frontend design — an entire topic, deliberately deferred.** Not yet discussed: visual
language for the two applications, whether they share a foundation, density, typography,
light or dark, motion for live data. The reasoning for deferring it: a layout cannot be
designed for content whose shape has not been seen, and the shape of a diagnostic answer
— how many findings, how long, how many citations, whether a chart dominates — is
currently a guess. It becomes an artifact after M4. Same for the HMI: whether a
six-second takt is legible and whether states flicker too fast to read are empirical
questions about a machine that does not exist yet.

One idea worth banking now, needed at M6: **the HMI's colour system can encode the
cause/consequence distinction directly.** Colour by category rather than by state —
producing, waiting-on-others, held-by-own-fault, stopped, transitioning — so anyone
glancing at the line sees red on the station that *is* the problem and amber on the ones
merely waiting. The visual language would teach the diagnostic model. Colour must never
be the only channel, per ISA-101 and for the obvious accessibility reason.
