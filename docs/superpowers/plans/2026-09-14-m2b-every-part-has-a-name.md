# M2b — Every Part Has a Name — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every component and assembly is individually identified, genealogy is as-built, and S2's press is recorded against the serial at the instant of production — so any serial can be traced end to end.

**Architecture:** Four new OPC UA event types carry identity across the boundary. `PartState` — deliberately thin in M2a — becomes the assembly record that rides the carrier. S2 generates a force–distance curve with two independent knobs, because a later scenario turns on separating a material problem from a press problem when peak and distance agree. Migration `003` adds the identity tables; `inspection_results` reaches §5.2's full shape in one ALTER.

**Tech Stack:** Python 3.13 · asyncua 2.0.1 · .NET 10 / UA-.NETStandard 1.5.378.176 · PostgreSQL 17 · React 19

**Spec:** `docs/superpowers/specs/2026-09-13-m2-the-plant-is-real-design.md` (decisions D6, D11, D12) implementing §3.1, §3.4a and §5.2 of `docs/superpowers/specs/2026-09-12-machine-diagnostics-assistant-design.md`. **Read both.**

---

## Global Constraints

- **Only OPC UA crosses between the two stacks.** Exactly two containers join `field-net`. `plant-hmi` joins `plant-net` + `plant-edge`.
- **`SourceTimestamp` is simulated time and is what all analysis uses.** `DateTime.Now` is banned; in Python use the injected clock.
- **Two uv workspaces.** Never `pytest`/`dotnet` from the repository root.
- **Every number is configuration.**
- `mypy --strict` with `disallow_any_explicit`. No `Any`. Every suppression carries a rule code **and** a reason.
- **Do not catch an exception you cannot specifically recover from.** Three places have a real recovery: the gateway's OPC UA reconnect, its local queue when Postgres is unreachable, and the agent's tool loop.
- Do not swallow `asyncio.CancelledError` / `OperationCanceledException`.
- **Do not write a test that asserts how the code works.**
- **A cache in `PostgresWriter` may only hold what a committed transaction put there** — filled batch-locally, promoted after `CommitAsync`, because the drain retries with nothing acked.
- `make check` green before every commit. Commits: conventional prefix, body saying WHY, ending `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Never `--no-verify`.

---

## What M2a left, that this milestone builds on

- 25 historised streams, discovered and subscribed under a per-signal policy that **fails open**.
- `PartState` on the `Line`, keyed by carrier id, carrying one optional `disposition`. M2a's own comment says it is kept thin so the next milestone's data model does not arrive early and unvalidated. **That is this milestone.**
- `EVENT_FIELDS` unchanged since M1 — one event type, six fields, `DefectClass` singular.
- `HistoryEventPageSize = 25`, forced by image bytes against a 4 MiB limit. **Four new event types carry no images.**
- The truncation guard is shared by both read paths and tested against a fake reproducing the measured server.
- `backfill_windows.stream` is `<station code>.<signal>`, never the browse name.

---

## Scope

**In:** component serials and lots per lane; assembly serials created at S1 and marked at S2; as-built genealogy; per-part process values and force–distance curves; the four new event types; migration `003`; D11's widening of `inspection_results`; D12's three live-only nodes; the gateway ingest for all of it; the HMI part strip.

**Out:** scenarios, faults, the noise floor, ground truth, alarms — all M2c. Any *diagnosis* — M3.

**§3.4 already requires six independent per-class scores that do not sum to 1**, and two classes on one part. M1's event carries one `DefectClass` and one `Confidence`. D11 widens the table; this milestone must widen the **event** too, or the table has columns nothing fills.

---

## File structure

```
plant/simulator/src/simulator/
  identity.py          NEW  serial formats, the lot schedule, the carrier's assembly record
  curve.py             NEW  §3.4a's force–distance curve, two independent knobs
  events.py            MOD  five event types, not one
  address_space.py     MOD  the four event generators; D12's three live-only nodes
  line.py              MOD  PartState becomes the assembly record
  stations/s1_feeding.py   MOD  read two components, create the assembly, emit two events
  stations/s2_joining.py   MOD  press against the serial; emit the curve
  stations/s3_inspection.py MOD  the widened inspection event
  stations/s4_outfeed.py   MOD  emit the disposition event
  config.py            MOD  lot size, component defect rate, curve parameters
  hmi.py               MOD  the part strip's payload

diagnostics/gateway/Gateway/
  Migrations/003_m2b.sql   NEW
  Opc/Subscriptions.cs     MOD  four event filters, not one
  Opc/HistoryBackfill.cs   MOD  per-event-type page size
  Ingest/PostgresWriter.cs MOD  the identity write path

diagnostics/analysis/src/analysis/
  routes_parts.py      MOD  GET /parts/{serial} returns the full trace
  models.py            MOD

plant/hmi/src/PartStrip.tsx  NEW
contracts/analysis.openapi.yaml  MOD
```

---

## Task 1: The identity model, and the lot schedule

Pure logic, no I/O. Serial formats, how components are drawn from a lot, and the record that rides the carrier.

**§3.1's asymmetry is the point:** components are individually serialised and belong to a supplier lot; the assembly gets its own serial, created at S1 and physically marked at S2. Three records per assembly instead of one — roughly 32,000 rows for an 18-hour history, which is nothing. Lot-level containment survives *and* exact as-built genealogy is gained on top.

**Files:** Create `plant/simulator/src/simulator/identity.py`, `plant/simulator/tests/test_identity.py`. Modify `config.py`.

**Interfaces produced:** `ComponentSerial`/`AssemblySerial` formatting; `Lot(lot_code: str, lane: int, supplier: str)`; `LotSchedule` yielding the lot a lane is drawing from at a given simulated instant; `Assembly(serial: str, carrier_id: int, created_at: datetime, components: tuple[Component, ...])`; `Component(serial: str, lot_code: str, lane: int, read_at: datetime)`.

- [ ] **Step 1: Write the failing tests**

```python
"""§3.1's identities. Components belong to lots; assemblies belong to themselves."""


def test_a_component_serial_names_its_lane_and_is_unique() -> None:
    """Lane is in the serial because containment questions start from a lane."""


def test_two_lanes_draw_from_different_lots_at_the_same_instant() -> None:
    """§3.5 scenario 7 contaminates ONE lane's lot. If both lanes shared a lot the
    scenario could not be expressed, and scenario 5 (lane contamination) collapses
    into it."""


def test_a_lot_is_exhausted_before_the_next_one_starts() -> None:
    """Lots are consumed in order and depleted_at is when the next began. An
    overlapping schedule would make 'which lot was this component from' ambiguous
    at exactly the moment a containment query needs it."""


def test_an_assembly_records_the_components_it_was_built_from() -> None:
    """As-built, at the instant of assembly. §3.4a: the association is known exactly
    at the instant of production and only approximately afterwards."""


def test_the_same_seed_produces_the_same_lots_and_serials() -> None:
    """§3.6, and the property M2a had to fix once already — seed derivation must not
    use hash(), which is PYTHONHASHSEED-salted."""
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_identity.py -v
```

- [ ] **Step 3: Write `identity.py`**

Serial formats: `C-{lane}-{index:08d}` for components, `A-{index:08d}` for assemblies (**keep M2a's `serial_for` format for assemblies** — the gateway, the analysis service and the M1 chat citation all already resolve it, and changing it would break a working citation for cosmetics).

`LotSchedule` advances a lane to its next lot after `settings.lot_size` components. Lot codes follow §3.5 scenario 7's shape — `L-4471` — so the scenario has a name to inject.

Seed per lane with `zlib.crc32`, never `hash()`.

- [ ] **Step 4: Add the numbers to configuration**

```python
    # §3.1's identity model. Lot size sets how many parts a contaminated lot touches,
    # which is what M2c's scenario 7 containment list is scored against; too large and
    # every part is in the lot, too small and the correlation has no power.
    lot_size: int = 500
    lot_code_prefix: str = "L-"
    supplier_count: int = 3
```

- [ ] **Step 5: Run to verify they pass, then `make check`**
- [ ] **Step 6: Commit** — `feat(plant): components belong to lots, assemblies belong to themselves`

---

## Task 2: The force–distance curve

§3.4a: *where* force begins to rise is about the incoming components — an undersized component lets the press travel further before it meets resistance — while *how* load develops after contact is about the press.

**Two independent knobs, or M2c's scenario 7 is unwinnable by construction.** Scenario 7 is a bad component lot with a *perfectly stable* joining force; scenario 3 is a force drift. Both raise `gap` defects. The only thing separating them is curve *shape* while peak and distance agree. §3.5 calls scenario 7 the strongest test in the set precisely because the symptom points at the wrong cause.

**Files:** Create `plant/simulator/src/simulator/curve.py`, `plant/simulator/tests/test_curve.py`. Modify `config.py`.

**Interfaces produced:** `force_distance(rng, contact_mm: float, stiffness: float, samples: int) -> tuple[float, ...]`, plus `peak_of(curve)` and `distance_of(curve)` so the two summaries are **derived from the curve** rather than generated beside it.

- [ ] **Step 1: Write the failing tests**

```python
def test_peak_and_distance_are_derived_from_the_curve_not_generated_beside_it() -> None:
    """If they were generated independently they could disagree with the curve that
    is supposed to summarise them, and every scenario-7 query would be reading two
    unrelated numbers."""


def test_a_later_contact_point_moves_the_curve_without_moving_the_peak() -> None:
    """The scenario-7 signal: an undersized component lets the press travel further
    before resistance, and the press still reaches its target force. If this test
    cannot be made to pass, scenario 7 has no signal and M2c must be told."""
    late = force_distance(rng(), contact_mm=8.0, stiffness=NOMINAL, samples=40)
    early = force_distance(rng(), contact_mm=6.0, stiffness=NOMINAL, samples=40)
    assert peak_of(late) == pytest.approx(peak_of(early), rel=0.02)
    assert first_rise(late) > first_rise(early)


def test_a_softer_press_moves_the_peak_without_moving_the_contact_point() -> None:
    """The scenario-3 signal, and the mirror of the test above. The two knobs must be
    separable in both directions or the analysis cannot tell them apart either."""


def test_the_curve_is_monotonic_until_contact_and_rises_after() -> None:
    """A force trace that wanders before contact is noise pretending to be physics."""
```

- [ ] **Step 2–5: fail, implement, pass, `make check`**

The curve is `samples` points: flat (sensor noise only) until `contact_mm`, then rising with `stiffness` to the target force. **Both knobs are configuration**, with a comment naming which scenario reads which.

- [ ] **Step 6: Commit** — `feat(plant): a press curve whose contact point and stiffness move independently`

---

## Task 3: Five event types where there was one

M1's `EVENT_FIELDS` is one tuple. §4.1 gives S1 two events, S2 one, S3 one, S4 one.

**And §3.4 requires the inspection event to widen**: six independent per-class scores that do not sum to 1, and two classes on one part (scenarios 4 and 5 each need a pair; pattern DP-02 is keyed on a pair). M1's single `DefectClass`/`Confidence` cannot express it, and D11's widened table would otherwise have columns nothing fills.

**Files:** Modify `events.py`, `address_space.py`, `stations/*.py`, `tests/test_address_space.py`. 

**The ORDER MATTERS constraint applies to every generator:** `get_event_generator` must run before `historize_node_event`, or event history is created with no columns. M1 measured this; it now applies five times.

- [ ] **Step 1: Write the failing tests**

```python
def test_every_event_type_historises_with_all_its_columns() -> None:
    """M1's ORDER MATTERS defect, five times over: a generator created after
    historize_node_event yields an event table with no columns and no error."""


def test_the_inspection_event_carries_a_vector_not_a_scalar() -> None:
    """§3.4: six independent scores in [0,1] that do NOT sum to 1, because scenario 6
    needs all six to fall together — impossible under a softmax — and scenarios 4 and
    5 need two classes on one part."""


def test_a_good_part_scores_low_on_all_six_and_is_confident() -> None:
    """The exact defect §3.4 records: reading the vector as a distribution reported a
    good part as 27% confident and ~30% misaligned."""


def test_the_four_new_event_types_carry_no_image() -> None:
    """Only rejects carry an image, and only from S3. Task 4's page sizing depends on
    this being true, not merely intended."""
```

- [ ] **Step 2–5.** Event shapes:

| Event | Emitting node | Fields |
|---|---|---|
| `ComponentReadEvent` | S1 | `ComponentSerial`, `Lane`, `LotCode`, `ReadAt` |
| `AssemblyCreatedEvent` | S1 | `AssemblySerial`, `ComponentSerials` (array), `CarrierId` |
| `PartProcessedEvent` | S2 | `AssemblySerial`, `Curve` (array of Double), `PeakForce`, `JoiningDistance` |
| `InspectionResultEvent` | S3 | `AssemblySerial`, `CarrierId`, `Disposition`, `DefectClasses` (array), `Confidences` (array), `Confidence`, `ModelVersion`, `Image` |
| `PartCompletedEvent` | S4 | `AssemblySerial`, `Disposition`, `Reason` |

**`Confidence` (scalar) stays alongside `Confidences` (vector)** — §3.4 is explicit that the scalar is confidence in the OK/NOK *verdict*, not in a class, and conflating them is the measured defect.

D12: add `Lane1_Lot`, `Lane2_Lot`, `CurrentAssemblySerial` as **live-only** nodes — present in the tree, never historised, because the events carry the same facts authoritatively and a historised second copy invites the time-join §3.4a forbids. **The browse-count test from M2a must be updated: 37 variables, 25 historised, 9 static, 3 live-only.**

- [ ] **Step 6: Commit** — `feat(plant): the four events identity needs, and an inspection result that is a vector`

---

## Task 4: The stations produce identity

S1 reads two components and creates the assembly; S2 presses against the serial; S3 inspects the assembly on its carrier; S4 dispositions it.

`PartState` becomes the assembly record. **Keep it exactly as wide as the events need and no wider** — M2a's comment on it is the standard.

**Files:** Modify `line.py`, all four `stations/*.py`, `inspection_client.py`, their tests.

- [ ] **Step 1: Write the failing tests**

```python
async def test_a_part_carries_its_serial_from_s1_to_s4() -> None:
    """The whole milestone in one test."""

async def test_the_assembly_records_the_two_components_it_was_built_from() -> None:

async def test_s2_presses_against_the_serial_not_against_the_clock() -> None:
    """§3.4a: the association is known exactly at the instant of production. A press
    recorded against a timestamp and joined later is the inference the spec forbids."""

async def test_s4_refuses_a_part_whose_serial_it_never_saw() -> None:
    """M2a's S4 refuses a part with no disposition. The same standard for identity:
    sorting a part nobody can name is a quiet wrong answer."""
```

- [ ] **Step 2–6.** `InspectionClient` returns a `PartOutcome` widened to the vector. **D7 applies: the classifier owns false-accept and false-reject** — they are its configured rates, not the noise model's, so a real `ModelClassifier` does not double-count them.

Commit — `feat(plant): every part is named at S1 and answers to it at S4`

---

## Task 5: Migration 003 and the identity write path

**Files:** Create `diagnostics/gateway/Gateway/Migrations/003_m2b.sql`. Modify `PostgresWriter.cs`, `PostgresWriterTests.cs`.

```sql
-- M2b's slice of §5.2.
CREATE TABLE IF NOT EXISTS component_lots (
  id         SMALLSERIAL PRIMARY KEY,
  lot_code   TEXT NOT NULL,
  lane       SMALLINT NOT NULL,
  supplier   TEXT NOT NULL,
  loaded_at  TIMESTAMPTZ NOT NULL,
  depleted_at TIMESTAMPTZ,
  UNIQUE (lot_code, lane)
);

CREATE TABLE IF NOT EXISTS components (
  serial  TEXT PRIMARY KEY,
  lot_id  SMALLINT NOT NULL REFERENCES component_lots(id),
  lane    SMALLINT NOT NULL,
  read_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS assemblies (
  serial     TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL,
  carrier_id SMALLINT NOT NULL REFERENCES carriers(id)
);

-- As-built structure. §3.4a: known exactly at the instant of production.
CREATE TABLE IF NOT EXISTS genealogy (
  assembly_serial  TEXT NOT NULL REFERENCES assemblies(serial),
  component_serial TEXT NOT NULL REFERENCES components(serial),
  position         SMALLINT NOT NULL,
  PRIMARY KEY (assembly_serial, component_serial)
);
CREATE INDEX IF NOT EXISTS genealogy_component ON genealogy (component_serial);

CREATE TABLE IF NOT EXISTS part_station_events (
  assembly_serial TEXT NOT NULL REFERENCES assemblies(serial),
  station_id      SMALLINT NOT NULL REFERENCES stations(id),
  entered_at      TIMESTAMPTZ NOT NULL,
  left_at         TIMESTAMPTZ,
  state_at_entry  TEXT,
  PRIMARY KEY (assembly_serial, station_id)
);

-- Authoritative per part (§3.4a). The time series still exists for trend questions;
-- this row is what the part itself is asked about.
CREATE TABLE IF NOT EXISTS part_process_values (
  assembly_serial TEXT NOT NULL REFERENCES assemblies(serial),
  station_id      SMALLINT NOT NULL REFERENCES stations(id),
  signal          TEXT NOT NULL,
  value           DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (assembly_serial, station_id, signal)
);

-- D6: the curve, not the two scalars, is what separates a press problem from a
-- material problem. An array rather than 40 rows per part.
CREATE TABLE IF NOT EXISTS part_process_curves (
  assembly_serial TEXT NOT NULL REFERENCES assemblies(serial),
  station_id      SMALLINT NOT NULL REFERENCES stations(id),
  signal          TEXT NOT NULL,
  samples         DOUBLE PRECISION[] NOT NULL,
  PRIMARY KEY (assembly_serial, station_id, signal)
);

CREATE TABLE IF NOT EXISTS part_dispositions (
  assembly_serial TEXT PRIMARY KEY REFERENCES assemblies(serial),
  at              TIMESTAMPTZ NOT NULL,
  disposition     TEXT NOT NULL,
  reason          TEXT
);

-- D11: inspection_results reaches §5.2's shape in ONE alter, here, rather than one
-- when carriers arrived and another when the vector did.
ALTER TABLE inspection_results ADD COLUMN IF NOT EXISTS carrier_id SMALLINT REFERENCES carriers(id);
ALTER TABLE inspection_results ADD COLUMN IF NOT EXISTS defect_classes TEXT[];
ALTER TABLE inspection_results ADD COLUMN IF NOT EXISTS confidences DOUBLE PRECISION[];
ALTER TABLE inspection_results ADD COLUMN IF NOT EXISTS positions TEXT[];
```

**`carriers` finally fills** — M2a created it empty because the address space exposes no carrier nodes. `AssemblyCreatedEvent` carries `CarrierId`, so the writer upserts it, using `INSERT ... SELECT ... WHERE NOT EXISTS` (`carriers.id` is a plain `SMALLINT` primary key here, but follow the established shape).

**Foreign-key ordering is the trap.** `genealogy` references both `assemblies` and `components`; `AssemblyCreatedEvent` and `ComponentReadEvent` are separate events that may arrive in either order — **and the backfill reads history one node at a time, so a whole stream of one arrives before any of the other.** Either defer the constraint, or land identity rows in dependency order within the batch, or accept the rows and reconcile. **Decide, test the out-of-order case explicitly, and say what you chose** — M2a's `to_state` nullability came from exactly this, and getting it wrong means an unacknowledgeable record retried forever with the queue growing.

- [ ] Steps: failing tests (including out-of-order arrival), migration, writer routing, `make check`, commit.

Commit — `feat(gateway): genealogy, and the ordering two event streams cannot promise`

---

## Task 6: The gateway ingests four more event types

**Files:** Modify `Subscriptions.cs`, `HistoryBackfill.cs`, `SignalPolicy.cs`, `config/signals.json`, tests.

M1's `BuildInspectionFilter()` builds one `EventFilter` whose `SelectClauses` match one type. Four more are needed, each with its own field order — **and that order IS the wire format**, because an event notification arrives as a positional `EventFieldList`. M1's comment says this is stated independently of the plant on purpose: *a check that moves when the thing it checks moves proves nothing.*

**Page size per event type.** `HistoryEventPageSize = 25` exists solely because inspection events carry images against a 4 MiB response limit. The four new types carry none — paging them at 25 costs ~3,200 round trips per stream where 80 would do. The policy file already has `page_size`; extend it to event types, and **note that M2a measured `PartProcessedEvent`'s shape as the risk**: a 40-sample curve per part is ~320 bytes, so a 1,000-row page is ~320 KB — fine, but derive the number rather than assuming it.

**The truncation guard already covers both paths.** Verify by test that it covers the new types too — M2a found the buffer `Level` streams truncating at exactly the page size with no continuation point, which nothing predicted.

### The gateway will discover 28 streams where the plant historises 25

`TopologyDiscovery.DiscoverStationsAsync` takes **every** variable child of a station as a signal to subscribe to, and **never reads the `Historizing` attribute**. D12's three live-only nodes — `Lane1_Lot`, `Lane2_Lot`, `CurrentAssemblySerial` — are children of S1.

So the gateway subscribes to 28, backfills 28, and reconciles three of them against a ledger that has no rows for them. **This will not show up in the plant's browse-count test** — that asserts the plant's own tree and stays green whatever the gateway does. It shows up as a reconciliation failure under `make verify`, which is not part of `make check`.

Read `Historizing` during discovery and skip what the plant does not historise. A node the plant declares live-only is a node the plant is telling you not to store; ingesting it anyway is the gateway deciding it knows better.

- [ ] Steps as above. Commit — `feat(gateway): four event types, each paged for what it carries`

---

## Task 7: Any serial, traced end to end

§14's line: *"Any serial can be traced end to end: genealogy, station history, the process values recorded for that part, inspection result, disposition."*

**Files:** Modify `diagnostics/analysis/src/analysis/routes_parts.py`, `models.py`, `contracts/analysis.openapi.yaml`, tests.

**One endpoint is already silently wrong and must be fixed here.** `/inspection/stats` groups by `defect_class`, a column the widened inspection event no longer feeds. The chain: the plant sends `DefectClasses` (a vector); the gateway's select clause still names the old scalar `DefectClass`; asyncua maps an unresolvable select clause to `Variant(None)` rather than erroring; the writer resolves that to `DBNull`; and the endpoint's `defect_class IS NOT NULL` filter then empties the group-by. **No exception, no 500 — `by_defect_class: []` while `total`, `rejects` and `sample_serials` stay correct.** A silently empty answer to "which defects are we seeing" is the exact failure mode §1 exists to prevent, and `make check` cannot see it because the analysis tests seed their own rows. Move the endpoint to the array columns, and give it a test whose fixture comes from the widened shape.

**The anti-shortcut is the authenticity proof.** M1 already asserts `GET /parts/{serial}` reads `inspection_results` directly and performs no time-range join. Extend that to genealogy and process values: a test that fails if the read path ever reconstructs "which part was at S2 at 02:14:07" instead of reading the row written at the instant of production. §3.4a is explicit that inferred traceability is what makes a containment list unusable at the moment it matters.

`contracts/` is the single source of truth and the UI's generated types are checked against it — **CLAUDE.md says ask before changing `contracts/`. This plan is that ask, and the change is: `GET /parts/{serial}` gains genealogy, process values, the curve and disposition.**

- [ ] Steps: failing contract test, schema, route, regenerate types, `make check`, commit.

Commit — `feat(analysis): a serial answers with its whole history, joined to nothing`

---

## Task 8: The part strip, and what M2b proves

**Files:** Create `plant/hmi/src/PartStrip.tsx`, its test. Modify `hmi.py`, `plant/hmi/src/snapshot.ts`, `measurements/authenticity/README.md`, `README.md`.

The HMI gains the last parts with their serials; rejects show a thumbnail (§3.7). Keep it undesigned — visual language is still deferred.

**M2b's authenticity proof:** take any assembly serial from the database and resolve its complete history — two component serials with their lots, four station visits, the press curve, the inspection verdict with its class vector, the disposition — **and assert the read path issued no time-range join**. That proof is what §14's traceability line means, and it is the one M3's containment query will rest on.

Update `measurements/authenticity/README.md`: M2b makes this proof, and moves *"a containment query returns exactly the affected serials"* from "waits on M2b + M7" to "waits on M7" — the genealogy it needed now exists.

- [ ] Steps. Commit — `test(plant): a serial answers for its whole life, and nothing infers it`

---

## Self-review

**Spec coverage:** D6 → Task 5's `part_process_curves`; D7 → Task 4's classifier; D11 → Task 5's single ALTER; D12 → Task 3's live-only nodes. §3.1's asymmetric identities → Tasks 1, 4. §3.4a's curve and its anti-reconstruction rule → Tasks 2, 7. §3.4's vector → Task 3. §5.2's seven identity tables → Task 5. §14's traceability line → Tasks 7, 8.

**Known traps carried from M2a:** seed with `crc32`, never `hash()`; the ORDER MATTERS generator/historise sequence, now five times; a writer cache may only hold what a committed transaction put there; `backfill_windows.stream` uses the station code; the truncation guard must cover new streams; page size comes from the policy; every comment asserting a measured fact must have been measured.

**Open question this plan does NOT settle, deliberately:** the foreign-key ordering between `assemblies`, `components` and `genealogy` when the backfill reads one node at a time. Task 5 decides and records it, because the right answer depends on what the reader measures.
