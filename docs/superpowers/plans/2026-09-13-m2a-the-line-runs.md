# M2a — The Line Runs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Four stations under PackML, joined by three buffers with twelve carriers circulating, producing through catch-up and live — with state and buffer data landing in Postgres and visible on a thin HMI.

**Architecture:** The plant's single takt loop becomes a **discrete-event queue**: `(next_event_time, station_index)` popped in time order, ties broken by index. That is what keeps §3.6's seeded reproducibility once four stations share buffers and a carrier pool — four concurrent `asyncio` loops drawing from one RNG are not reproducible. The queue also collapses catch-up and live into one mechanism, extending M1's phase-based clock rather than replacing it. On the diagnostics side the gateway's hardcoded two-signal subscription becomes a **discovered** signal set governed by a policy file that fails open.

**Tech Stack:** Python 3.13 · asyncua 2.0.1 · FastAPI/uvicorn (new to the `simulator` package, already resolved in the workspace) · .NET 10 / UA-.NETStandard 1.5.378.176 · PostgreSQL 17 · React 19 / Vite 8 / pnpm

**Spec:** `docs/superpowers/specs/2026-09-13-m2-the-plant-is-real-design.md`, which implements §§3.1–3.3, 4.1 and 5.2 of `docs/superpowers/specs/2026-09-12-machine-diagnostics-assistant-design.md`. **Read both.** The design doc's numbered decisions D1–D13 are referenced by number throughout and are not restated here.

---

## Global Constraints

Copied verbatim from the spec and `CLAUDE.md`. Every task's requirements implicitly include this section.

- **Only OPC UA crosses between the two stacks.** Exactly two containers join `field-net`: `line-simulator` and `edge-gateway`. `plant-hmi` joins **`plant-net`**. No shared database, no shared volume, no second protocol, no writes to the plant.
- **The diagnostics stack must work with the plant stack shut down.**
- **`SourceTimestamp` is simulated time and is what all analysis uses.** `ServerTimestamp` is the wall clock, diagnostics only. `DateTime.Now` is banned at build time; in Python use the injected clock (`SimulatedClock.now()` / `.wall`).
- **Two uv workspaces, one per stack.** Never run `pytest` or `dotnet` from the repository root.
- **Every number is configuration** — takt, speeds, thresholds, deadbands, budgets, seeds.
- **Ground truth never reaches the diagnostics stack.** (M2a produces none; M2c does.)
- `mypy --strict` with `disallow_any_explicit`. **Do not use `Any` or `any` to make a type error go away.**
- **Do not catch an exception you cannot specifically recover from.** The three places with a real recovery are the gateway's OPC UA reconnect, its local queue when Postgres is unreachable, and the agent's tool loop. Nothing in M2a adds a fourth.
- **Do not swallow cancellation.** Re-raise `asyncio.CancelledError` and `OperationCanceledException`.
- **Do not add an interface, factory or base class with one implementation.** `stations/base.py` has four; that is the justification.
- **Do not write a test that asserts how the code works.** If a refactor with no behaviour change breaks the test, the test was wrong.
- Every suppression carries a rule code and a reason (`PGH003`, `RUF100`, `warn_unused_ignores`).
- Commits: conventional prefix, why-not-what body, ending `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Never `--no-verify`. Never push unless asked.
- **`make check` green before every commit.**

### Commands

```
make check                                     lint + types + tests — the gate
make fmt                                       format in place
cd plant && uv run --package simulator pytest simulator/tests/test_x.py::test_y
cd diagnostics/gateway && dotnet test --filter FullyQualifiedName~TestName
```

---

## Scope

**In:** the four stations, PackML, buffers, carriers, the discrete-event queue, catch-up and live at the new stream count, migration `002`, topology discovery of buffer references, the per-signal policy file, per-event-type page sizing, the thin HMI, and the propagation proof.

**Out, and where it goes:** component and assembly serials, lots, genealogy, force–distance curves and the four new event types are **M2b**. Scenarios, the noise floor, alarms, ground truth and fault injection are **M2c**. Any diagnosis of anything is **M3**.

**The inspection event does not change in M2a.** It keeps M1's `EVENT_FIELDS` exactly. D11 widens `inspection_results` in M2b, in one ALTER; M2a's inspection results deliberately carry no `carrier_id` even though carriers exist by then, because history regenerates from scratch on every boot and the only question needing the link is M2c's scenario 4.

---

## File structure

```
plant/simulator/src/simulator/
  packml.py            NEW  the 15 states, transitions, and the Suspended reason pair
  buffers.py           NEW  Level/Capacity, and the starved/blocked determination
  carriers.py          NEW  the circulating pool
  line.py              NEW  the discrete-event queue, and the line the stations live on
  stations/
    __init__.py        NEW
    base.py            NEW  takt loop, signal writing, ledger accounting
    s1_feeding.py      NEW
    s2_joining.py      NEW
    s3_inspection.py   NEW  the inspection call, moved out of station_s3.py
    s4_outfeed.py      NEW
  station_s3.py        DELETED — its contents split across line.py and stations/
  address_space.py     MOD  §4.1's tree: four stations, three buffers, State/StateReason
  config.py            MOD  buffer capacity, carrier count, station takts
  historian.py         MOD  the ledger becomes per-stream rather than three named fields
  hmi.py               NEW  the WebSocket the HMI reads (D5, in-process)
  server.py            MOD  builds a Line rather than calling station_s3 directly

plant/simulator/tests/
  test_packml.py       NEW    test_buffers.py       NEW
  test_carriers.py     NEW    test_line_queue.py    NEW
  test_stations.py     NEW    test_propagation.py   NEW  the M2a authenticity proof
  test_address_space.py  MOD  test_generation.py    MOD
  test_hmi.py          NEW

diagnostics/gateway/Gateway/
  Migrations/002_m2a.sql   NEW  buffers, carriers, state_changes, buffer_levels
  Opc/SignalPolicy.cs      NEW  D3's policy file, failing open
  Opc/AddressSpace.cs      MOD  the full discovered topology, not three hardcoded paths
  Opc/TopologyDiscovery.cs MOD  buffer references → the buffers table
  Opc/Subscriptions.cs     MOD  discovered signal set, policy-driven deadbands
  Opc/HistoryBackfill.cs   MOD  per-stream page size from the policy
  Ingest/PostgresWriter.cs MOD  state_changes and buffer_levels derived rows

plant/hmi/                 NEW  React/Vite, mirroring diagnostics/ui's toolchain
diagnostics/gateway/Gateway/config/signals.json  NEW  the policy file
measurements/run_r5.py     NEW  Task 1's historian throughput probe
measurements/authenticity/README.md  NEW  the file M1 marked done and never wrote
```

**Why `station_s3.py` is deleted rather than extended.** It is 358 lines holding the takt loop, catch-up generation, live production and S3's specifics together. Four stations multiply all four concerns. Its careful comments are not lost — every one of them moves to the module that inherits the behaviour it explains, and several are load-bearing (the `SourceTimestamp` suppression, the queue-cap pacing, the forward-looking takt convention).

---

## Task 1: Measure the historian at 25 streams before anything depends on it

M1 opened with R1–R4 and every number moved the design. One number cannot be predicted from M1's results: **historian write throughput during catch-up at 25 streams rather than 3.**

The reasoning for expecting improvement is real — rendering dominated M1's 151–185 s wall and D10's 1.5% reject rate cuts rendered images by ~70% — but §12 says M2 compounds the truncation defects, and this project's standard is to have the number rather than the argument. This task also settles D9 (whether unbounded retention stays free) by measuring the file size.

**Files:**
- Create: `measurements/run_r5.py`
- Create: `measurements/r5-streams.json` (written by the runner)
- Modify: `Makefile` — add `m2a-r5` alongside `m1-report`

**Interfaces:**
- Produces: `measure(streams: int, rows_per_stream: int, batch_size: int, pause_s: float) -> R5Result`, where `R5Result` is a frozen dataclass with fields `wall_seconds: float`, `rows_written: int`, `rows_expected: int`, `db_bytes: int`, `dropped: int`. Task 7 consumes the measured `batch_size`/`pause_s` as `Settings.catchup_batch_size` / `catchup_batch_pause_seconds`.

- [ ] **Step 1: Write the failing measurement test**

`measurements/` is checked with the plant's interpreter (see `lint-python`), so the test lives with the plant's suite.

Create `plant/simulator/tests/test_r5_runner.py`:

```python
"""R5's runner is measurement code, so it is tested for correctness of the
*measurement*, not for the value it produces -- a threshold here would make the
gate depend on the machine it runs on."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "measurements"))

from run_r5 import measure  # noqa: E402


@pytest.mark.asyncio
async def test_the_probe_reports_every_row_it_asked_for(tmp_path: Path) -> None:
    """The whole point of R5 is catching silent loss, so a probe that cannot tell
    written from expected proves nothing."""
    result = await measure(
        streams=4,
        rows_per_stream=50,
        batch_size=20,
        pause_s=0.0,
        db_path=tmp_path / "r5.db",
    )
    assert result.rows_expected == 200
    assert result.rows_written == 200
    assert result.dropped == 0


@pytest.mark.asyncio
async def test_the_probe_detects_loss_rather_than_reporting_success(
    tmp_path: Path,
) -> None:
    """asyncua's per-item queue caps at 10,000 and discards the oldest. A probe that
    reports a clean run while rows are missing is the defect M1 found, reproduced in
    the tool meant to find it."""
    result = await measure(
        streams=1,
        rows_per_stream=12_000,
        batch_size=12_000,  # one uninterrupted burst, no chance to drain
        pause_s=0.0,
        db_path=tmp_path / "r5-overflow.db",
    )
    assert result.dropped > 0
    assert result.rows_written < result.rows_expected
```

- [ ] **Step 2: Run it to watch it fail**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_r5_runner.py -v
```

Expected: `ModuleNotFoundError: No module named 'run_r5'`.

- [ ] **Step 3: Write the runner**

Create `measurements/run_r5.py`:

```python
"""R5 -- historian write throughput at M2's stream count (§12's compounding row).

M1 measured three streams. §4.1 counts to 25 historised ones (see the M2 design
doc §2), and all three truncation defects M1 found scale with that number. This
probe answers two questions before any station code is written: how long the
historian takes to absorb a full catch-up at 25 streams, and how large the file
gets -- which is what settles D9's "unbounded retention is still free".

It writes through a real HistorySQLite, not a mock. A mock would measure the mock.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from asyncua import Server, ua
from asyncua.server.history_sql import HistorySQLite


@dataclass(frozen=True)
class R5Result:
    streams: int
    rows_per_stream: int
    batch_size: int
    pause_s: float
    wall_seconds: float
    rows_expected: int
    rows_written: int
    dropped: int
    db_bytes: int


async def measure(
    streams: int, rows_per_stream: int, batch_size: int, pause_s: float, db_path: Path
) -> R5Result:
    """Write `rows_per_stream` values into each of `streams` historised variables,
    pacing in batches, and report what the historian actually holds afterwards."""
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://127.0.0.1:48400/r5")
    idx = await server.register_namespace("http://machine-agent/r5")

    storage = HistorySQLite(str(db_path))
    server.iserver.history_manager.set_storage(storage)

    folder = await server.nodes.objects.add_object(idx, "R5")
    nodes = [
        await folder.add_variable(idx, f"S{i:02d}", 0.0, ua.VariantType.Double)
        for i in range(streams)
    ]
    for node in nodes:
        await server.historize_node_data_change(node, period=None, count=0)

    async with server:
        start_ts = datetime.now(UTC) - timedelta(seconds=rows_per_stream)
        began = time.monotonic()
        written = 0
        for row in range(rows_per_stream):
            sim_ts = start_ts + timedelta(seconds=row)
            for stream, node in enumerate(nodes):
                # The value must differ every write or asyncua's monitored-item
                # filter coalesces it away before the storage layer sees it -- the
                # same defect station_s3._next_takt exists to dodge.
                await node.write_value(
                    ua.DataValue(
                        ua.Variant(
                            float(row * streams + stream), ua.VariantType.Double
                        ),
                        SourceTimestamp=sim_ts,  # type: ignore[arg-type]
                    )
                )
                written += 1
            if (row + 1) % batch_size == 0 and pause_s > 0:
                await asyncio.sleep(pause_s)

        # Let the ~10 ms publish loop drain whatever is still queued before counting.
        await asyncio.sleep(max(pause_s, 0.5))
        stored = 0
        for node in nodes:
            table = storage._get_table_name(node.nodeid)
            async with storage._db.execute(f'SELECT COUNT(*) FROM "{table}"') as cursor:
                row_count = await cursor.fetchone()
            stored += int(row_count[0]) if row_count else 0
        wall = time.monotonic() - began

    return R5Result(
        streams=streams,
        rows_per_stream=rows_per_stream,
        batch_size=batch_size,
        pause_s=pause_s,
        wall_seconds=round(wall, 3),
        rows_expected=written,
        rows_written=stored,
        dropped=written - stored,
        db_bytes=db_path.stat().st_size,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--streams", type=int, default=25)
    # 33 h at a 6 s takt, matching what catch-up will actually generate.
    parser.add_argument("--rows-per-stream", type=int, default=19_800)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--pause", type=float, default=0.05)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parent / "r5-streams.json"
    )
    parser.add_argument("--db", type=Path, default=Path("/tmp/r5-streams.db"))
    args = parser.parse_args()

    args.db.unlink(missing_ok=True)
    result = await measure(
        streams=args.streams,
        rows_per_stream=args.rows_per_stream,
        batch_size=args.batch_size,
        pause_s=args.pause,
        db_path=args.db,
    )
    args.out.write_text(json.dumps(asdict(result), indent=2) + "\n")
    print(json.dumps(asdict(result), indent=2))
    if result.dropped:
        raise SystemExit(
            f"R5 FAIL: {result.dropped} of {result.rows_expected} rows never reached "
            "the historian. Lower --batch-size or raise --pause before building on this."
        )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_r5_runner.py -v
```

Expected: PASS, 2 tests. The second takes ~20 s — it deliberately overflows the queue.

- [ ] **Step 5: Run the real measurement and read the number**

```bash
cd plant && uv run --package simulator python ../measurements/run_r5.py
```

Expected: `r5-streams.json` written, exit 0. **Record three things in the commit message:** `wall_seconds`, `db_bytes`, and whether `dropped` was 0 at the M1-inherited pacing (`batch_size=500`, `pause=0.05`).

**If `dropped > 0`:** the pacing M1 measured for 3 streams does not hold for 25. Bisect `--batch-size` downward (250, 125) until `dropped` is 0 across three consecutive runs, and carry that value into Task 7's `Settings.catchup_batch_size`. Do not raise the queue size instead — the cap is asyncua's and discarding the oldest is its behaviour, not a setting.

**If `db_bytes` exceeds ~500 MB:** D9's "unbounded retention is still free" no longer holds. Stop and say so rather than choosing a retention period — the real-clock-versus-simulated-clock trap in D9 means any period shorter than the history depth erases history as it is written.

- [ ] **Step 6: Add the Makefile target**

In `Makefile`, after the `m1-report` target:

```make
# R5: the one number M1's results cannot predict -- historian throughput at M2's stream
# count. Run before Task 2, not after the line is built, so a bad number changes the
# design rather than the excuses.
m2a-r5:
	cd plant && uv run --frozen --package simulator python $(CURDIR)/measurements/run_r5.py
```

- [ ] **Step 7: Commit**

```bash
git add measurements/run_r5.py measurements/r5-streams.json \
        plant/simulator/tests/test_r5_runner.py Makefile
git commit
```

Message: `test(measurements): R5 — what the historian does at twenty-five streams`, with the three recorded numbers in the body and an explicit statement of whether the M1 pacing survived.

---
## Task 2: PackML, and the reason field that is not optional

§3.3 adopts PackML *semantics and structure* per ISA-TR88.00.02 — fifteen states, omitting `Completing` and `Complete`, which belong to batch operation. We do **not** claim conformance with the OPC UA companion specification (OPC 30050), and the code says so.

The semantics do the diagnostic work, so they are the thing to get right:

| State | Entered when | Meaning for diagnosis |
|---|---|---|
| `Suspended` | upstream buffer empty (`starved`) or downstream buffer full (`blocked`) | **consequence, by definition** — clears itself |
| `Held` | own condition needing an operator, e.g. a jam | **cause candidate** — requires Unhold |
| `Aborted` | fault shutdown | **cause candidate** — requires Clearing then Reset |

**Files:**
- Create: `plant/simulator/src/simulator/packml.py`
- Test: `plant/simulator/tests/test_packml.py`

**Interfaces:**
- Produces: `State` (str enum, 15 members), `Command` (str enum), `SuspendReason(direction: Literal["starved", "blocked"], buffer_id: str)`, and `StateMachine` with `.state -> State`, `.reason -> str`, `.apply(command: Command, reason: SuspendReason | str | None = None) -> State`, `.settle() -> State | None`. Task 3 consumes `SuspendReason`; Task 5 consumes all of it.

- [ ] **Step 1: Write the failing tests**

Create `plant/simulator/tests/test_packml.py`:

```python
"""§3.3's state machine. These assert the *semantics* -- which states are reachable
from which, and that a Suspended transition cannot exist without naming its buffer --
never the shape of the transition table, which is free to change."""

from __future__ import annotations

import pytest

from simulator.packml import Command, State, StateMachine, SuspendReason


def running() -> StateMachine:
    """A machine in Execute, reached the way a real one reaches it."""
    machine = StateMachine()
    machine.apply(Command.CLEAR)
    machine.settle()
    machine.apply(Command.RESET)
    machine.settle()
    machine.apply(Command.START)
    machine.settle()
    return machine


def test_there_are_exactly_fifteen_states_and_batch_operation_is_not_among_them() -> (
    None
):
    """§3.3: the subset that applies to a continuously running line. Completing and
    Complete belong to batch operation and their absence is the claim being made."""
    assert len(State) == 15
    assert not {"Completing", "Complete"} & {state.value for state in State}


def test_a_cold_machine_reaches_execute_through_the_states_isa_requires() -> None:
    machine = StateMachine()
    assert machine.state is State.ABORTED

    machine.apply(Command.CLEAR)
    assert machine.state is State.CLEARING
    assert machine.settle() is State.STOPPED

    machine.apply(Command.RESET)
    assert machine.state is State.RESETTING
    assert machine.settle() is State.IDLE

    machine.apply(Command.START)
    assert machine.state is State.STARTING
    assert machine.settle() is State.EXECUTE


def test_suspending_without_naming_a_buffer_is_refused() -> None:
    """§3.3: 'Every Suspended transition records which buffer and which direction.
    That field is what makes propagation verifiable rather than inferred, so it is
    not optional.' A machine that can enter Suspended with an empty reason makes the
    whole propagation claim unverifiable."""
    machine = running()
    with pytest.raises(ValueError, match="reason"):
        machine.apply(Command.SUSPEND)


def test_a_suspended_station_names_its_buffer_and_its_direction() -> None:
    machine = running()
    machine.apply(Command.SUSPEND, SuspendReason("starved", "B2_3"))
    machine.settle()
    assert machine.state is State.SUSPENDED
    assert machine.reason == "starved:B2_3"


def test_suspended_clears_itself_and_held_does_not() -> None:
    """The distinction the whole diagnostic model rests on: Suspended is a
    consequence and resolves when the buffer does; Held is a cause candidate and
    waits for an operator."""
    suspended = running()
    suspended.apply(Command.SUSPEND, SuspendReason("blocked", "B3_4"))
    suspended.settle()
    assert suspended.clears_itself is True

    held = running()
    held.apply(Command.HOLD, "jam")
    held.settle()
    assert held.state is State.HELD
    assert held.clears_itself is False


def test_leaving_suspended_clears_the_reason() -> None:
    """A stale reason on a running station would read as a station still waiting."""
    machine = running()
    machine.apply(Command.SUSPEND, SuspendReason("starved", "B1_2"))
    machine.settle()
    machine.apply(Command.UNSUSPEND)
    assert machine.settle() is State.EXECUTE
    assert machine.reason == ""


def test_aborting_is_reachable_from_every_state() -> None:
    """A fault shutdown does not wait for the machine to be in a convenient state."""
    for state in State:
        machine = StateMachine(state)
        machine.apply(Command.ABORT)
        assert machine.state is State.ABORTING
        assert machine.settle() is State.ABORTED


def test_a_command_the_current_state_does_not_accept_raises() -> None:
    """Silently ignoring an illegal command is how a station ends up in a state
    nothing put it in."""
    machine = StateMachine()  # Aborted
    with pytest.raises(ValueError, match="Start"):
        machine.apply(Command.START)


def test_settle_does_nothing_in_a_waiting_state() -> None:
    machine = running()
    assert machine.settle() is None
    assert machine.state is State.EXECUTE
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_packml.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'simulator.packml'`.

- [ ] **Step 3: Write the state machine**

Create `plant/simulator/src/simulator/packml.py`:

```python
"""PackML per ISA-TR88.00.02 (§3.3), in the subset that applies to a continuously
running line.

We adopt PackML *semantics and structure*, not the OPC UA companion specification
(OPC 30050). An automation engineer recognises the model immediately; this project
does not claim conformance, and §3.3 says so openly.

Fifteen states: `Completing` and `Complete` are omitted because they describe a batch
finishing, and this line never finishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class State(str, Enum):
    # Waiting states -- the machine sits here until commanded.
    ABORTED = "Aborted"
    STOPPED = "Stopped"
    IDLE = "Idle"
    EXECUTE = "Execute"
    HELD = "Held"
    SUSPENDED = "Suspended"
    # Acting states -- the machine passes through, and settle() advances it.
    ABORTING = "Aborting"
    CLEARING = "Clearing"
    STOPPING = "Stopping"
    RESETTING = "Resetting"
    STARTING = "Starting"
    HOLDING = "Holding"
    UNHOLDING = "Unholding"
    SUSPENDING = "Suspending"
    UNSUSPENDING = "Unsuspending"


class Command(str, Enum):
    ABORT = "Abort"
    CLEAR = "Clear"
    STOP = "Stop"
    RESET = "Reset"
    START = "Start"
    HOLD = "Hold"
    UNHOLD = "Unhold"
    SUSPEND = "Suspend"
    UNSUSPEND = "Unsuspend"


@dataclass(frozen=True)
class SuspendReason:
    """§3.3: which buffer, and which direction. Both, always.

    `starved` means the upstream buffer is empty; `blocked` means the downstream one
    is full. Rendering as `direction:buffer_id` keeps the OPC UA `StateReason` node a
    plain string while still carrying two fields, which is what §4.1's address space
    declares it to be.
    """

    direction: Literal["starved", "blocked"]
    buffer_id: str

    def __str__(self) -> str:
        return f"{self.direction}:{self.buffer_id}"


_SETTLES_TO: dict[State, State] = {
    State.ABORTING: State.ABORTED,
    State.CLEARING: State.STOPPED,
    State.STOPPING: State.STOPPED,
    State.RESETTING: State.IDLE,
    State.STARTING: State.EXECUTE,
    State.HOLDING: State.HELD,
    State.UNHOLDING: State.EXECUTE,
    State.SUSPENDING: State.SUSPENDED,
    State.UNSUSPENDING: State.EXECUTE,
}

_ACCEPTS: dict[Command, frozenset[State]] = {
    Command.CLEAR: frozenset({State.ABORTED}),
    Command.RESET: frozenset({State.STOPPED}),
    Command.START: frozenset({State.IDLE}),
    Command.HOLD: frozenset({State.EXECUTE}),
    Command.UNHOLD: frozenset({State.HELD}),
    Command.SUSPEND: frozenset({State.EXECUTE}),
    Command.UNSUSPEND: frozenset({State.SUSPENDED}),
    # Stop and Abort are accepted anywhere: a fault shutdown does not wait for the
    # machine to reach a convenient state, and neither does an operator.
    Command.STOP: frozenset(State),
    Command.ABORT: frozenset(State),
}

_ENTERS: dict[Command, State] = {
    Command.CLEAR: State.CLEARING,
    Command.RESET: State.RESETTING,
    Command.START: State.STARTING,
    Command.HOLD: State.HOLDING,
    Command.UNHOLD: State.UNHOLDING,
    Command.SUSPEND: State.SUSPENDING,
    Command.UNSUSPEND: State.UNSUSPENDING,
    Command.STOP: State.STOPPING,
    Command.ABORT: State.ABORTING,
}

_CARRIES_REASON = frozenset(
    {State.SUSPENDING, State.SUSPENDED, State.HOLDING, State.HELD}
)


class StateMachine:
    """One station's PackML state. Pure logic: no clock, no I/O, no address space.

    Starts in `Aborted` because that is where a machine that has never been cleared
    genuinely is -- starting in `Execute` would mean every station's history begins
    with a transition nothing caused.
    """

    def __init__(self, state: State = State.ABORTED) -> None:
        self._state = state
        self._reason = ""

    @property
    def state(self) -> State:
        return self._state

    @property
    def reason(self) -> str:
        """The `StateReason` node's value: empty unless the state carries one."""
        return self._reason

    @property
    def clears_itself(self) -> bool:
        """§3.3's cause/consequence split, as a property rather than a comment.

        `Suspended` is a consequence by definition and resolves when the buffer that
        caused it does. `Held` and `Aborted` are cause candidates and need an
        operator. M3's analysis turns on exactly this distinction, and the plant is
        where it is decided.
        """
        return self._state in (State.SUSPENDED, State.SUSPENDING)

    def apply(
        self, command: Command, reason: SuspendReason | str | None = None
    ) -> State:
        """Raises ValueError if the current state does not accept `command`, or if a
        reason-carrying transition was given none."""
        if self._state not in _ACCEPTS[command]:
            raise ValueError(f"{command.value} is not accepted in {self._state.value}")

        entering = _ENTERS[command]
        if entering in _CARRIES_REASON:
            if reason is None:
                raise ValueError(
                    f"{command.value} needs a reason: §3.3 makes the field that names "
                    "which buffer and which direction non-optional, because it is what "
                    "makes propagation verifiable rather than inferred"
                )
            self._reason = str(reason)
        else:
            self._reason = ""

        self._state = entering
        return self._state

    def settle(self) -> State | None:
        """Advance an acting state to the waiting state it leads to.

        Returns the new state, or None if the machine was already waiting. Acting
        states are instantaneous here -- a station that took measurable wall time to
        start would be modelling motor spin-up, which no scenario in §3.5 turns on.
        """
        settled = _SETTLES_TO.get(self._state)
        if settled is None:
            return None
        self._state = settled
        if settled not in _CARRIES_REASON:
            self._reason = ""
        return settled
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_packml.py -v
```

Expected: PASS, 9 tests.

- [ ] **Step 5: Run the gate**

```bash
make check
```

Expected: green. `mypy --strict` must accept `packml.py` with no suppressions — it is pure logic with no asyncua types in it, so any `type: ignore` here is a design smell rather than an SDK limitation.

- [ ] **Step 6: Commit**

```bash
git add plant/simulator/src/simulator/packml.py plant/simulator/tests/test_packml.py
git commit
```

Message: `feat(plant): PackML's fifteen states, and the reason a Suspended transition cannot omit`. The body should say why `Suspended` carrying its buffer is enforced by a raise rather than a convention: §3.3 calls the field non-optional because propagation is otherwise inferred rather than verified, and M3's whole analysis rests on it.

---

## Task 3: Buffers and carriers — the things that make propagation delayed rather than instant

§3.1's defaults, all configurable: **12 carriers · 6 s takt · buffer capacity 5.** Buffer capacity is the number that matters — it sets how long propagation takes to become visible. Five carriers at 6 s means S3 starves roughly 30 s after S2 stops, and reasoning about that delay is exactly the analysis's job.

Carriers **circulate**. Without that, a worn carrier passes once and M2c's carrier-wear scenario has no statistical signal to find.

**Files:**
- Create: `plant/simulator/src/simulator/buffers.py`, `plant/simulator/src/simulator/carriers.py`
- Modify: `plant/simulator/src/simulator/config.py` — carrier count, buffer capacity, **and the per-station takts §3.1 now requires**
- Test: `plant/simulator/tests/test_buffers.py`, `plant/simulator/tests/test_carriers.py`

**Interfaces:**
- Consumes: `SuspendReason` from Task 2.
- Produces: `Buffer(buffer_id: str, capacity: int, upstream: str, downstream: str)` with `.level -> int`, `.is_empty -> bool`, `.is_full -> bool`, `.put(carrier: Carrier) -> None`, `.take() -> Carrier`; `suspend_reason_for(upstream: Buffer | None, downstream: Buffer | None) -> SuspendReason | None`; `Carrier(carrier_id: int)` and `CarrierPool(count: int)` with `.acquire() -> Carrier | None`, `.release(carrier: Carrier) -> None`, `.available -> int`, `.count -> int`.

- [ ] **Step 1: Write the failing buffer tests**

Create `plant/simulator/tests/test_buffers.py`:

```python
"""§3.1's buffers, and the starved/blocked rule §3.3 derives from them."""

from __future__ import annotations

import pytest

from simulator.buffers import Buffer, suspend_reason_for
from simulator.carriers import Carrier


def test_a_buffer_knows_which_stations_it_sits_between() -> None:
    """§4.1: buffer nodes reference the stations they sit between, and the gateway
    reads those references to discover topology. A buffer that did not carry them
    would make the topology configured rather than discovered."""
    buffer = Buffer("B2_3", capacity=5, upstream="S2", downstream="S3")
    assert (buffer.upstream, buffer.downstream) == ("S2", "S3")


def test_taking_from_an_empty_buffer_is_refused() -> None:
    """A level that can go negative is a level that stops meaning anything."""
    buffer = Buffer("B1_2", capacity=5, upstream="S1", downstream="S2")
    with pytest.raises(ValueError, match="empty"):
        buffer.take()


def test_putting_into_a_full_buffer_is_refused() -> None:
    buffer = Buffer("B1_2", capacity=2, upstream="S1", downstream="S2")
    buffer.put(Carrier(0))
    buffer.put(Carrier(1))
    with pytest.raises(ValueError, match="full"):
        buffer.put(Carrier(2))


def test_a_buffer_returns_carriers_in_the_order_it_received_them() -> None:
    """A buffer is a queue on a conveyor, not a stack. LIFO here would let one
    carrier sit at the bottom for the whole run, which is the same statistical
    blindness as carriers that do not circulate at all."""
    buffer = Buffer("B1_2", capacity=3, upstream="S1", downstream="S2")
    buffer.put(Carrier(7))
    buffer.put(Carrier(9))
    assert buffer.take().carrier_id == 7
    assert buffer.take().carrier_id == 9


def test_an_empty_upstream_buffer_starves_the_station_below_it() -> None:
    upstream = Buffer("B2_3", capacity=5, upstream="S2", downstream="S3")
    reason = suspend_reason_for(upstream=upstream, downstream=None)
    assert reason is not None
    assert (reason.direction, reason.buffer_id) == ("starved", "B2_3")


def test_a_full_downstream_buffer_blocks_the_station_above_it() -> None:
    downstream = Buffer("B3_4", capacity=1, upstream="S3", downstream="S4")
    downstream.put(Carrier(0))
    reason = suspend_reason_for(upstream=None, downstream=downstream)
    assert reason is not None
    assert (reason.direction, reason.buffer_id) == ("blocked", "B3_4")


def test_starvation_is_reported_before_blockage_when_both_hold() -> None:
    """Both can be true at once on a line that has just stopped. The station has no
    part to work on either way, and 'starved' names the condition that arrived
    first -- reporting 'blocked' would point diagnosis downstream of a line that is
    actually short of parts."""
    upstream = Buffer("B2_3", capacity=5, upstream="S2", downstream="S3")
    downstream = Buffer("B3_4", capacity=1, upstream="S3", downstream="S4")
    downstream.put(Carrier(0))
    reason = suspend_reason_for(upstream=upstream, downstream=downstream)
    assert reason is not None
    assert reason.direction == "starved"


def test_a_station_with_work_and_room_has_no_reason_to_suspend() -> None:
    upstream = Buffer("B2_3", capacity=5, upstream="S2", downstream="S3")
    upstream.put(Carrier(0))
    downstream = Buffer("B3_4", capacity=5, upstream="S3", downstream="S4")
    assert suspend_reason_for(upstream=upstream, downstream=downstream) is None


def test_the_ends_of_the_line_have_no_buffer_on_their_outer_side() -> None:
    """S1 is fed by lanes and S4 discharges to outfeed, so each has one side with no
    buffer at all. Neither may be reported as suspended for a buffer that is not
    there -- M2c's scenarios 1 and 2 are precisely faults on those outer sides."""
    assert suspend_reason_for(upstream=None, downstream=None) is None
```

- [ ] **Step 2: Write the failing carrier tests**

Create `plant/simulator/tests/test_carriers.py`:

```python
"""§3.1's circulating carrier pool."""

from __future__ import annotations

from simulator.carriers import CarrierPool


def test_the_pool_hands_out_the_carriers_it_was_built_with() -> None:
    pool = CarrierPool(count=3)
    assert pool.count == 3
    assert pool.available == 3
    first = pool.acquire()
    assert first is not None
    assert pool.available == 2


def test_an_exhausted_pool_reports_none_rather_than_inventing_a_carrier() -> None:
    """S1 with no free carrier must stop, not conjure one. A pool that grows on
    demand would make the carrier count decorative."""
    pool = CarrierPool(count=1)
    assert pool.acquire() is not None
    assert pool.acquire() is None


def test_carriers_circulate_rather_than_passing_once() -> None:
    """§3.1: 'Carriers circulate -- without that, a worn carrier passes once and the
    carrier-wear scenario has no statistical signal to find.' Over four times the
    pool size, every carrier must be used a comparable number of times."""
    pool = CarrierPool(count=4)
    seen: list[int] = []
    for _ in range(16):
        carrier = pool.acquire()
        assert carrier is not None
        seen.append(carrier.carrier_id)
        pool.release(carrier)

    counts = {carrier_id: seen.count(carrier_id) for carrier_id in range(4)}
    assert set(counts) == {0, 1, 2, 3}
    assert max(counts.values()) - min(counts.values()) <= 1


def test_a_released_carrier_goes_to_the_back_of_the_queue() -> None:
    """FIFO rather than LIFO: a stack would recirculate one carrier while the rest
    sat idle, which is the same statistical blindness as not circulating at all."""
    pool = CarrierPool(count=3)
    first = pool.acquire()
    assert first is not None
    pool.release(first)
    second = pool.acquire()
    assert second is not None
    assert second.carrier_id != first.carrier_id
```

- [ ] **Step 3: Run both to verify they fail**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_buffers.py simulator/tests/test_carriers.py -v
```

Expected: collection errors for `simulator.buffers` and `simulator.carriers`.

- [ ] **Step 4: Write the buffers**

Create `plant/simulator/src/simulator/buffers.py`:

```python
"""§3.1's buffers, and the starved/blocked rule §3.3 derives from their levels.

Buffer capacity is the number that sets how long propagation takes to become
visible: five carriers at a 6 s takt means S3 starves roughly 30 s after S2 stops.
That delay is what M3's analysis has to reason about, so it is configuration
(`Settings.buffer_capacity`) rather than a constant here.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from simulator.carriers import Carrier
from simulator.packml import SuspendReason


@dataclass
class Buffer:
    """One buffer between two stations. It holds carriers, not a count.

    A count would be enough for M2a, where nothing yet asks which carrier is where.
    It would not survive M2b: an assembly serial is created at S1 and travels on its
    carrier, so "which part is at S3" has to be answerable from the buffer itself
    rather than reconstructed by time-joining the takt series -- which is exactly the
    inference §3.4a forbids.

    `upstream` and `downstream` are station codes rather than station objects: §4.1
    exposes them as OPC UA references the gateway browses to discover the line's
    topology, and a code is what crosses that wire.
    """

    buffer_id: str
    capacity: int
    upstream: str
    downstream: str
    _carriers: deque[Carrier] = field(default_factory=deque)

    @property
    def level(self) -> int:
        """§4.1's `Level` node. Derived from the contents rather than tracked
        alongside them, so the two can never disagree."""
        return len(self._carriers)

    @property
    def is_empty(self) -> bool:
        return self.level == 0

    @property
    def is_full(self) -> bool:
        return self.level >= self.capacity

    def put(self, carrier: Carrier) -> None:
        if self.is_full:
            raise ValueError(f"{self.buffer_id} is full at {self.capacity}")
        self._carriers.append(carrier)

    def take(self) -> Carrier:
        """FIFO: a buffer is a queue on a conveyor, not a stack."""
        if self.is_empty:
            raise ValueError(f"{self.buffer_id} is empty")
        return self._carriers.popleft()


def suspend_reason_for(
    upstream: Buffer | None, downstream: Buffer | None
) -> SuspendReason | None:
    """§3.3's rule: a station is `starved` when the buffer feeding it is empty, and
    `blocked` when the buffer it discharges into is full.

    `None` on either side means the line ends there -- S1 is fed by feeder lanes and
    S4 discharges to outfeed -- and a station is never suspended for a buffer that
    does not exist. M2c's scenarios 1 and 2 inject faults on exactly those outer
    sides, and they must present as something other than a buffer condition.

    Starvation wins when both hold. The station has no part to work on either way,
    and naming the downstream blockage would point diagnosis at the wrong end of a
    line that is actually short of parts.
    """
    if upstream is not None and upstream.is_empty:
        return SuspendReason("starved", upstream.buffer_id)
    if downstream is not None and downstream.is_full:
        return SuspendReason("blocked", downstream.buffer_id)
    return None
```

- [ ] **Step 5: Write the carriers**

Create `plant/simulator/src/simulator/carriers.py`:

```python
"""§3.1's circulating pool of carriers.

The circulation is the point. A carrier that passes once carries its wear out of the
population with it, and M2c's scenario 4 -- carrier 7 wears, and `misalignment` plus
`scratch` concentrate on it -- has no statistical signal to find. FIFO rather than a
stack for the same reason: a stack would recirculate whichever carrier came back last
while the rest sat idle.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Carrier:
    carrier_id: int


class CarrierPool:
    def __init__(self, count: int) -> None:
        if count < 1:
            raise ValueError(f"a line needs at least one carrier, got {count}")
        self._count = count
        self._free: deque[Carrier] = deque(Carrier(i) for i in range(count))

    @property
    def count(self) -> int:
        return self._count

    @property
    def available(self) -> int:
        return len(self._free)

    def acquire(self) -> Carrier | None:
        """None when every carrier is in the line. S1 must then stop rather than
        conjure one -- a pool that grew on demand would make the carrier count
        decorative, and §3.1 makes it one of the three numbers that set line
        behaviour."""
        return self._free.popleft() if self._free else None

    def release(self, carrier: Carrier) -> None:
        self._free.append(carrier)
```

- [ ] **Step 6: Add the three numbers to configuration**

In `plant/simulator/src/simulator/config.py`, inside `Settings`, after the `# line` block:

```python
    # §3.1's three line defaults. Buffer capacity is the one that matters: it sets how
    # long propagation takes to become visible, and Task 12's authenticity proof
    # measures exactly the delay it produces (5 x 6 s ~= 30 s from S2 stopping to S3
    # starving). Changing it changes that proof's expected value, which is why the
    # proof derives the number rather than hardcoding 30.
    carrier_count: int = 12
    buffer_capacity: int = 5

    # §3.1: the stations do NOT share one takt. S3 is the slowest and paces the line
    # at the 6 s every other number is quoted against; S1 and S2 run faster so their
    # buffers fill, and S4 matches S3 so B3_4 stays near empty without S4 starving on
    # every single cycle.
    #
    # A balanced line would make buffer capacity bound nothing -- every buffer would
    # oscillate between empty and one, because each station consumes exactly as fast
    # as the one above produces. The bottleneck is what gives a buffer a level to
    # hold, and the level is what makes propagation delayed rather than immediate.
    #
    # Starting values, confirmed by Task 12's measurement rather than assumed.
    station_takt_seconds: dict[str, float] = {
        "S1": 5.70,
        "S2": 5.85,
        "S3": 6.00,
        "S4": 6.00,
    }
```

**These values are a starting point with a stated intent, and Task 12 confirms them.** Two buffers at capacity park 10 carriers, plus what the four stations hold — against a pool of 12, that is close to the floor. If Task 12 finds S1 suspending on `carrier-return` during normal running rather than only when the line backs up, the carrier count rises. §3.1 calls all three numbers configurable and this is why.

- [ ] **Step 7: Run both suites to verify they pass**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_buffers.py simulator/tests/test_carriers.py -v
```

Expected: PASS, 13 tests.

- [ ] **Step 8: Commit**

```bash
git add plant/simulator/src/simulator/buffers.py plant/simulator/src/simulator/carriers.py \
        plant/simulator/src/simulator/config.py \
        plant/simulator/tests/test_buffers.py plant/simulator/tests/test_carriers.py
git commit
```

Message: `feat(plant): buffers that delay propagation, and carriers that come back`. The body should carry §3.1's reason for circulation — a carrier that passes once takes its wear out of the population, and scenario 4 then has nothing to find.

---
## Task 4: The discrete-event queue, and the order that makes a conveyor a conveyor

D1: one priority queue of `(at, station_index)`, popped in time order. §3.6 requires that the same seed plus the same scenario reproduces the run exactly, and four concurrent `asyncio` loops drawing from one RNG cannot promise that.

**Ties within one instant run downstream-first.** This is the decision that makes the model physical rather than merely deterministic. Break ties upstream-first and, at `t=0`, S1 puts a carrier into B1_2, S2 immediately takes it, S3 takes it, S4 takes it — a part traverses the whole line in zero simulated time. Downstream-first means S4 finds B3_4 empty and starves, S3 starves, S2 starves, and only then does S1 load the first carrier. A part then advances exactly one station per takt, which is what a conveyor does.

**Why the carrier pool is the binding constraint, not B1_2.** With 12 carriers and three buffers of capacity 5 there are 15 buffer slots. Stop S4 and the line backs up: B3_4 takes 5, S3 holds 1, B2_3 takes 5 — twelve carriers are gone before B1_2 has taken a single one. So S1 suspends for want of a carrier, never for a full B1_2. That is a real upstream supply condition and it gets a real reason.

**Files:**
- Create: `plant/simulator/src/simulator/line.py`
- Test: `plant/simulator/tests/test_line_queue.py`

**Interfaces:**
- Consumes: `State`, `Command`, `SuspendReason`, `StateMachine` (Task 2); `Buffer`, `suspend_reason_for` (Task 3); `Carrier`, `CarrierPool` (Task 3).
- Produces: `CycleQueue` with `.schedule(at: datetime, station_index: int) -> None`, `.pop() -> tuple[datetime, int] | None`, `.__len__()`; the `StationCycle` Protocol; `PartState(disposition: str | None)`; `CycleOutcome(station_code: str, at: datetime, produced: bool, state: State, reason: str, next_at: datetime)`; `Line` with `.seed(start_ts: datetime) -> None`, `.step() -> CycleOutcome | None`, `.buffers -> Sequence[Buffer]`, `.machine_for(code: str) -> StateMachine`. Task 5 implements `StationCycle` four times; Task 7 drives `.step()`.

**On the Protocol and the one-implementation rule.** `CLAUDE.md` forbids an interface with one implementation. `StationCycle` has four, in Task 5. It is declared here rather than there because `Line` is what constrains its shape, and a protocol defined next to its consumer is the one that stays honest.

- [ ] **Step 1: Write the failing queue tests**

Create `plant/simulator/tests/test_line_queue.py`:

```python
"""D1's discrete-event queue, and the Line that runs on it.

The Line is exercised with a recording fake rather than the real stations: Task 5's
stations do I/O, and the behaviour under test here -- ordering, buffer movement,
suspension -- is entirely the Line's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from simulator.buffers import Buffer
from simulator.carriers import Carrier, CarrierPool
from simulator.line import CycleQueue, Line, PartState
from simulator.packml import State

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


@dataclass
class FakeStation:
    """Records what the Line asked it to do. Not a second implementation of
    StationCycle in the sense CLAUDE.md forbids -- a test double."""

    code: str
    takt: float = 6.0
    cycles: list[tuple[datetime, int]] = field(default_factory=list)
    states: list[tuple[datetime, State, str]] = field(default_factory=list)

    async def run_cycle(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        self.cycles.append((at, carrier.carrier_id))

    async def publish_state(self, at: datetime, state: State, reason: str) -> None:
        self.states.append((at, state, reason))

    def next_takt(self) -> float:
        return self.takt


def build_line(carriers: int = 12, capacity: int = 5) -> tuple[Line, list[FakeStation]]:
    stations = [FakeStation(code) for code in ("S1", "S2", "S3", "S4")]
    buffers = [
        Buffer("B1_2", capacity, "S1", "S2"),
        Buffer("B2_3", capacity, "S2", "S3"),
        Buffer("B3_4", capacity, "S3", "S4"),
    ]
    line = Line(stations=stations, buffers=buffers, carriers=CarrierPool(carriers))
    line.seed(T0)
    return line, stations


def test_the_queue_pops_in_time_order() -> None:
    queue = CycleQueue()
    queue.schedule(T0 + timedelta(seconds=12), 0)
    queue.schedule(T0, 0)
    queue.schedule(T0 + timedelta(seconds=6), 0)
    assert [queue.pop(), queue.pop(), queue.pop()] == [
        (T0, 0),
        (T0 + timedelta(seconds=6), 0),
        (T0 + timedelta(seconds=12), 0),
    ]


def test_stations_scheduled_at_the_same_instant_run_downstream_first() -> None:
    """The decision that makes this a conveyor. Upstream-first would let one part
    traverse all four stations in zero simulated time."""
    queue = CycleQueue()
    for index in (0, 1, 2, 3):
        queue.schedule(T0, index)
    assert [queue.pop(), queue.pop(), queue.pop(), queue.pop()] == [
        (T0, 3),
        (T0, 2),
        (T0, 1),
        (T0, 0),
    ]


def test_an_empty_queue_pops_none() -> None:
    assert CycleQueue().pop() is None


@pytest.mark.asyncio
async def test_a_part_advances_exactly_one_station_per_takt() -> None:
    """The observable consequence of downstream-first ordering."""
    line, stations = build_line()
    for _ in range(16):  # four instants, four stations each
        await line.step()

    assert [at for at, _ in stations[0].cycles] == [
        T0,
        T0 + timedelta(seconds=6),
        T0 + timedelta(seconds=12),
        T0 + timedelta(seconds=18),
    ]
    # S2 cannot run at T0 -- B1_2 is empty until S1 has loaded into it.
    assert stations[1].cycles[0][0] == T0 + timedelta(seconds=6)
    assert stations[2].cycles[0][0] == T0 + timedelta(seconds=12)
    assert stations[3].cycles[0][0] == T0 + timedelta(seconds=18)


@pytest.mark.asyncio
async def test_a_starved_station_says_which_buffer_starved_it() -> None:
    line, stations = build_line()
    await line.step()  # S4 first, and B3_4 is empty
    assert stations[3].states[-1][1] is State.SUSPENDED
    assert stations[3].states[-1][2] == "starved:B3_4"


@pytest.mark.asyncio
async def test_a_station_that_gets_its_buffer_back_resumes() -> None:
    line, stations = build_line()
    for _ in range(24):
        await line.step()
    assert line.machine_for("S4").state is State.EXECUTE
    assert line.machine_for("S4").reason == ""


@pytest.mark.asyncio
async def test_carriers_run_out_before_the_first_buffer_fills() -> None:
    """Twelve carriers against fifteen buffer slots: stopping the line exhausts the
    pool before B1_2 takes a single carrier, so S1 suspends for want of a carrier and
    never for a full B1_2. A model where B1_2 filled first would be describing a
    different line."""
    line, stations = build_line(carriers=12, capacity=5)
    line.hold("S4", "test jam")
    for _ in range(400):
        await line.step()

    assert line.machine_for("S1").state is State.SUSPENDED
    assert line.machine_for("S1").reason == "starved:carrier-return"
    assert line.buffers[0].level < line.buffers[0].capacity


@pytest.mark.asyncio
async def test_the_same_seed_produces_the_same_run() -> None:
    """§3.6. The queue is the only thing deciding order, so this is the property that
    would break the moment station loops became concurrent."""

    async def run() -> list[tuple[str, datetime, bool]]:
        line, _ = build_line()
        trace: list[tuple[str, datetime, bool]] = []
        for _ in range(64):
            outcome = await line.step()
            assert outcome is not None
            trace.append((outcome.station_code, outcome.at, outcome.produced))
        return trace

    assert await run() == await run()
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_line_queue.py -v
```

Expected: `ModuleNotFoundError: No module named 'simulator.line'`.

- [ ] **Step 3: Write the queue and the Line**

Create `plant/simulator/src/simulator/line.py`:

```python
"""D1: the line advances on a discrete-event queue, not on four concurrent loops.

§3.6 requires that the same seed plus the same scenario reproduces a run exactly.
Four `asyncio` tasks drawing from one RNG and racing on buffer levels cannot promise
that -- task interleaving orders the draws. A single queue popped in time order can,
and it handles independently jittered per-station takt naturally, which a fixed
global tick does not.

The queue also collapses catch-up and live into one mechanism: catch-up drains it as
fast as it can, live sleeps until each cycle's wall-clock equivalent (§4.3, and
Task 7).
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from simulator.buffers import Buffer, suspend_reason_for
from simulator.carriers import Carrier, CarrierPool
from simulator.packml import Command, State, StateMachine, SuspendReason

CARRIER_RETURN = "carrier-return"
"""What S1 names when it suspends for want of a free carrier.

Not one of §4.1's three buffers, and deliberately not dressed up as one. With twelve
carriers against fifteen buffer slots the pool is what actually binds when the line
backs up -- B1_2 never fills -- so a station that reported `starved:B1_2` here would
be naming a condition that is not true. The carrier return path is a genuine upstream
supply constraint; this is its name.
"""


class StationCycle(Protocol):
    """What the Line needs from a station. Four implementations, in `stations/`.

    Deliberately narrow: the Line owns movement -- which buffer a carrier comes from,
    which it goes to, whether the station may run at all -- and the station owns only
    what happens to a part while it is there. Splitting it the other way would put
    buffer logic in four places.
    """

    @property
    def code(self) -> str: ...

    async def run_cycle(
        self, at: datetime, carrier: Carrier, part: PartState
    ) -> None: ...

    async def publish_state(self, at: datetime, state: State, reason: str) -> None: ...

    def next_takt(self) -> float: ...


@dataclass
class PartState:
    """What is known about the part riding a carrier, while it rides.

    Keyed by carrier id on the Line rather than stored in the buffers, because that
    is what a real line does -- the carrier has a tag and the part is whatever is
    currently on it. S3 writes the disposition; S4 reads it to drive GoodCount and
    RejectCount (§4.1), which is the only cross-station fact M2a needs.

    M2b replaces this with a real assembly serial and its genealogy. It is kept this
    thin on purpose: anything richer here would be M2b's data model arriving early
    and unvalidated.
    """

    disposition: str | None = None


@dataclass(frozen=True)
class CycleOutcome:
    station_code: str
    at: datetime
    produced: bool
    state: State
    reason: str
    next_at: datetime


class CycleQueue:
    """Deterministic by construction: the heap key is `(at, -station_index)`."""

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, int]] = []

    def schedule(self, at: datetime, station_index: int) -> None:
        # -station_index, so that stations due at the same instant run downstream
        # first. Upstream-first would let a carrier loaded by S1 be taken by S2, S3
        # and S4 within one instant -- a part crossing the whole line in zero
        # simulated time. Downstream-first makes a part advance exactly one station
        # per takt, which is what a conveyor does. The third element is the index
        # itself, so the caller never has to un-negate it.
        heapq.heappush(self._heap, (at, -station_index, station_index))

    def pop(self) -> tuple[datetime, int] | None:
        if not self._heap:
            return None
        at, _, station_index = heapq.heappop(self._heap)
        return at, station_index

    def __len__(self) -> int:
        return len(self._heap)


class Line:
    """Four stations, three buffers, one circulating carrier pool, one queue.

    `stations` is in line order: index 0 feeds, index -1 discharges. `buffers[i]`
    sits between `stations[i]` and `stations[i+1]`, so a station's source and sink
    are derived from its position rather than configured -- which is the same claim
    §4.1 makes about the gateway discovering topology rather than being told it.
    """

    def __init__(
        self,
        stations: Sequence[StationCycle],
        buffers: Sequence[Buffer],
        carriers: CarrierPool,
    ) -> None:
        if len(buffers) != len(stations) - 1:
            raise ValueError(
                f"{len(stations)} stations need {len(stations) - 1} buffers between "
                f"them, got {len(buffers)}"
            )
        self._stations = list(stations)
        self._buffers = list(buffers)
        self._carriers = carriers
        self._queue = CycleQueue()
        # Carrier id -> the part currently riding it. A carrier holds at most one
        # part, so the id is a sufficient key and no buffer has to carry a payload.
        self._parts: dict[int, PartState] = {}
        # Every station starts Aborted and is brought up the way a real one is, so
        # the history of each begins with the transitions that actually happened.
        self._machines = {station.code: StateMachine() for station in stations}
        for machine in self._machines.values():
            machine.apply(Command.CLEAR)
            machine.settle()
            machine.apply(Command.RESET)
            machine.settle()
            machine.apply(Command.START)
            machine.settle()

    @property
    def buffers(self) -> Sequence[Buffer]:
        return self._buffers

    def machine_for(self, code: str) -> StateMachine:
        return self._machines[code]

    def seed(self, start_ts: datetime) -> None:
        """Schedule every station's first cycle at the same instant. Downstream-first
        ordering is what turns that into a line starting up rather than a part
        teleporting."""
        for index in range(len(self._stations)):
            self._queue.schedule(start_ts, index)

    def hold(self, code: str, reason: str) -> None:
        """Put a station into `Held` -- §3.3's cause candidate, which does not clear
        itself. M2c injects faults through this; M2a's propagation proof uses it to
        stop S2 and watch S3 starve."""
        machine = self._machines[code]
        machine.apply(Command.HOLD, reason)
        machine.settle()

    def unhold(self, code: str) -> None:
        machine = self._machines[code]
        machine.apply(Command.UNHOLD)
        machine.settle()

    def _source_and_sink(self, index: int) -> tuple[Buffer | None, Buffer | None]:
        upstream = self._buffers[index - 1] if index > 0 else None
        downstream = self._buffers[index] if index < len(self._buffers) else None
        return upstream, downstream

    async def step(self) -> CycleOutcome | None:
        """Pop the earliest due cycle and run it. None when the queue is empty."""
        due = self._queue.pop()
        if due is None:
            return None
        at, index = due

        station = self._stations[index]
        machine = self._machines[station.code]
        upstream, downstream = self._source_and_sink(index)
        takt = station.next_takt()

        # A Held or Aborted station does not cycle and does not clear itself (§3.3).
        # It still occupies its slot in the queue, so that unholding it resumes
        # production without the line having to be rebuilt.
        if machine.state in (State.HELD, State.ABORTED):
            next_at = at + timedelta(seconds=takt)
            self._queue.schedule(next_at, index)
            return CycleOutcome(
                station.code, at, False, machine.state, machine.reason, next_at
            )

        reason = self._suspend_reason(index, upstream, downstream)
        if reason is not None:
            if machine.state is State.EXECUTE:
                machine.apply(Command.SUSPEND, reason)
                machine.settle()
                await station.publish_state(at, machine.state, machine.reason)
            next_at = at + timedelta(seconds=takt)
            self._queue.schedule(next_at, index)
            return CycleOutcome(
                station.code, at, False, machine.state, machine.reason, next_at
            )

        if machine.state is State.SUSPENDED:
            machine.apply(Command.UNSUSPEND)
            machine.settle()
            await station.publish_state(at, machine.state, machine.reason)

        if upstream is None:
            acquired = self._carriers.acquire()
            # _suspend_reason returned None, so the pool was not empty. An assert
            # rather than a cast: if this ever fires, the suspend rule and the
            # movement below have drifted apart, which is worth a crash.
            assert acquired is not None
            carrier = acquired
            self._parts[carrier.carrier_id] = PartState()
        else:
            carrier = upstream.take()

        part = self._parts[carrier.carrier_id]
        await station.run_cycle(at, carrier, part)

        if downstream is None:
            del self._parts[carrier.carrier_id]
            self._carriers.release(carrier)
        else:
            downstream.put(carrier)

        next_at = at + timedelta(seconds=takt)
        self._queue.schedule(next_at, index)
        return CycleOutcome(
            station.code, at, True, machine.state, machine.reason, next_at
        )

    def _suspend_reason(
        self, index: int, upstream: Buffer | None, downstream: Buffer | None
    ) -> SuspendReason | None:
        """§3.3's buffer rule, plus the one condition that is not a buffer."""
        if upstream is None and self._carriers.available == 0:
            return SuspendReason("starved", CARRIER_RETURN)
        return suspend_reason_for(upstream, downstream)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_line_queue.py -v
```

Expected: PASS, 8 tests.

- [ ] **Step 5: Run the gate**

```bash
make check
```

- [ ] **Step 6: Commit**

```bash
git add plant/simulator/src/simulator/line.py plant/simulator/tests/test_line_queue.py
git commit
```

Message: `feat(plant): the line runs on one queue, and ties break downstream-first`. The body must carry both reasons — determinism (§3.6, and why four async loops cannot give it) and the tie-break (upstream-first lets a part cross the line in zero simulated time). Also record that the carrier pool, not B1_2, is what binds when the line backs up: twelve carriers against fifteen buffer slots.

---
## Task 5: One station base, four stations, and the end of `station_s3.py`

D2. `station_s3.py` is 358 lines holding the takt loop, catch-up generation, live production and S3's specifics together. Four stations multiply all four. The takt loop and catch-up move to Task 7; the per-station behaviour lands here.

**Nothing in M1's comments is dropped.** Three are load-bearing and must travel with the code they explain: the `SourceTimestamp` suppression (a `ua.DateTime` binds as an unsupported sqlite parameter type and every row vanishes with no visible error), the `_next_takt` resample guard (asyncua coalesces unchanged values before the historian sees them — and D13 deletes this in M2c, not here), and the forward-looking takt convention (the value written for part *i* is the interval before part *i+1*).

**Files:**
- Create: `plant/simulator/src/simulator/stations/__init__.py`, `base.py`, `s1_feeding.py`, `s2_joining.py`, `s3_inspection.py`, `s4_outfeed.py`
- Delete: `plant/simulator/src/simulator/station_s3.py`
- Test: `plant/simulator/tests/test_stations.py`

**Interfaces:**
- Consumes: `StationCycle`, `PartState` (Task 4); `Carrier` (Task 3); `State` (Task 2).
- Produces: `StationNodes` (the address-space handles one station writes to), `Station` (ABC implementing `StationCycle`, with abstract `on_part`), and `FeedingStation`, `JoiningStation`, `InspectionStation`, `OutfeedStation`. `PartOutcome` and `serial_for` move here from `station_s3.py` unchanged. Task 6 constructs `StationNodes`; Task 7 constructs the four stations.

- [ ] **Step 1: Write the failing station tests**

Create `plant/simulator/tests/test_stations.py`:

```python
"""The four stations' own behaviour. Node writes are captured through a recording
double for the address space, so these assert what each station *records*, never how
it reaches asyncua."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from simulator.carriers import Carrier
from simulator.line import PartState
from simulator.packml import State
from simulator.stations import (
    FeedingStation,
    InspectionStation,
    JoiningStation,
    OutfeedStation,
)
from simulator.stations.base import PartOutcome

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


class RecordingNodes:
    """Stands in for StationNodes. Records (signal, timestamp, value) per write and
    every event triggered."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.writes: list[tuple[str, datetime, float | int | str]] = []
        self.events: list[dict[str, object]] = []

    async def write(self, signal: str, at: datetime, value: float | int | str) -> None:
        self.writes.append((signal, at, value))

    async def trigger_event(self, at: datetime, fields: dict[str, object]) -> None:
        self.events.append(fields)

    def signals(self) -> set[str]:
        return {signal for signal, _, _ in self.writes}


@pytest.mark.asyncio
async def test_every_station_records_its_takt_and_its_part_count() -> None:
    """§4.1 gives all four the same two, and they are deliberately different in
    kind -- a noisy float where a deadband is meaningful, and a monotonic counter
    where one would silently lose parts."""
    for station, nodes in build_all():
        await station.run_cycle(T0, Carrier(0), PartState())
        assert {"TaktTime", "PartCount"} <= nodes.signals()


@pytest.mark.asyncio
async def test_part_count_is_monotonic_across_cycles() -> None:
    station, nodes = build_one(FeedingStation)
    for _ in range(3):
        await station.run_cycle(T0, Carrier(0), PartState())
    counts = [value for signal, _, value in nodes.writes if signal == "PartCount"]
    assert counts == [1, 2, 3]


@pytest.mark.asyncio
async def test_feeding_records_both_lane_fills() -> None:
    station, nodes = build_one(FeedingStation)
    await station.run_cycle(T0, Carrier(0), PartState())
    assert {"LaneFill_1", "LaneFill_2"} <= nodes.signals()


@pytest.mark.asyncio
async def test_joining_records_a_peak_force_and_a_distance() -> None:
    """§4.1's two S2 process signals. The force-distance curve behind them is M2b."""
    station, nodes = build_one(JoiningStation)
    await station.run_cycle(T0, Carrier(0), PartState())
    assert {"JoiningForcePeak", "JoiningDistance"} <= nodes.signals()


@pytest.mark.asyncio
async def test_inspection_puts_its_verdict_on_the_part() -> None:
    """S4 sorts on this. Without it GoodCount and RejectCount would have to be
    reconstructed by time-joining, which is the inference §3.4a forbids."""
    station, nodes = build_one(InspectionStation)
    part = PartState()
    await station.run_cycle(T0, Carrier(0), part)
    assert part.disposition in ("good", "reject")
    assert len(nodes.events) == 1


@pytest.mark.asyncio
async def test_only_rejects_carry_an_image() -> None:
    """§3.4, and it is what keeps images inside the single permitted channel."""
    station, nodes = build_one(InspectionStation, always_reject=True)
    await station.run_cycle(T0, Carrier(0), PartState())
    assert nodes.events[0]["Image"]

    good, good_nodes = build_one(InspectionStation, always_reject=False)
    await good.run_cycle(T0, Carrier(0), PartState())
    assert not good_nodes.events[0]["Image"]


@pytest.mark.asyncio
async def test_outfeed_counts_good_and_reject_separately() -> None:
    station, nodes = build_one(OutfeedStation)
    await station.run_cycle(T0, Carrier(0), PartState(disposition="good"))
    await station.run_cycle(T0, Carrier(1), PartState(disposition="reject"))
    await station.run_cycle(T0, Carrier(2), PartState(disposition="reject"))

    good = [v for s, _, v in nodes.writes if s == "GoodCount"]
    reject = [v for s, _, v in nodes.writes if s == "RejectCount"]
    assert good[-1] == 1
    assert reject[-1] == 2


@pytest.mark.asyncio
async def test_outfeed_refuses_a_part_nobody_inspected() -> None:
    """A part reaching S4 with no disposition means S3 did not run, and sorting it
    as good would be a quiet wrong answer -- exactly what this system exists not to
    give."""
    station, _ = build_one(OutfeedStation)
    with pytest.raises(ValueError, match="disposition"):
        await station.run_cycle(T0, Carrier(0), PartState())


@pytest.mark.asyncio
async def test_a_state_change_is_written_with_its_reason() -> None:
    station, nodes = build_one(FeedingStation)
    await station.publish_state(T0, State.SUSPENDED, "starved:carrier-return")
    assert ("State", T0, "Suspended") in nodes.writes
    assert ("StateReason", T0, "starved:carrier-return") in nodes.writes


def test_each_station_takes_its_own_nominal_takt() -> None:
    """§3.1: S3 paces the line and the stations above it run faster, so their buffers
    fill. One shared takt would leave every buffer oscillating between empty and one,
    and buffer capacity would bound nothing."""
    means = {}
    for factory in (FeedingStation, JoiningStation, InspectionStation, OutfeedStation):
        station, _ = build_one(factory)
        draws = [station.next_takt() for _ in range(500)]
        means[station.code] = sum(draws) / len(draws)

    assert means["S1"] < means["S2"] < means["S3"]
    assert means["S3"] == pytest.approx(means["S4"], abs=0.05)


def test_successive_takts_never_repeat() -> None:
    """Not realism: asyncua's monitored-item filter drops a notification whenever the
    written value is unchanged, so a repeated takt is a row that never reaches the
    historian. D13 deletes this guard in M2c, once the noise floor makes takt vary
    for real."""
    station, _ = build_one(FeedingStation)
    values = [station.next_takt() for _ in range(200)]
    assert all(a != b for a, b in zip(values, values[1:], strict=True))
```

Add the two builders at the top of the file, below `T0`:

```python
def build_one(
    factory: type, always_reject: bool | None = None
) -> tuple[object, RecordingNodes]:
    """Each station is constructed exactly as Task 7 constructs it, minus the real
    address space."""
    from simulator.config import Settings

    settings = Settings()
    code = {
        FeedingStation: "S1",
        JoiningStation: "S2",
        InspectionStation: "S3",
        OutfeedStation: "S4",
    }[factory]
    nodes = RecordingNodes(code)
    if factory is InspectionStation:

        async def produce(serial: str, at: datetime) -> PartOutcome:
            reject = bool(always_reject)
            return PartOutcome(
                disposition="reject" if reject else "good",
                defect_class="gap" if reject else None,
                confidence=0.9,
                image=b"PNG" if reject else None,
                model_version="test-1",
            )

        return InspectionStation(nodes, settings, seed=1, produce=produce), nodes
    return factory(nodes, settings, seed=1), nodes


def build_all() -> list[tuple[object, RecordingNodes]]:
    return [
        build_one(factory)
        for factory in (
            FeedingStation,
            JoiningStation,
            InspectionStation,
            OutfeedStation,
        )
    ]
```

- [ ] **Step 2: Run them to verify they fail**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_stations.py -v
```

Expected: `ModuleNotFoundError: No module named 'simulator.stations'`.

- [ ] **Step 3: Write the station base**

Create `plant/simulator/src/simulator/stations/base.py`:

```python
"""What every station does identically: keep its own RNG, jitter its takt, write
TaktTime and PartCount, and publish PackML state with its reason.

`StationNodes` is the seam. A station never touches asyncua directly -- it names a
signal and a value, and Task 6's address space decides which node that is. That is
what lets the four stations be tested without a server, and it is the reason the
`SourceTimestamp` suppression lives in exactly one place instead of four.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.line import PartState
from simulator.packml import State


@dataclass(frozen=True)
class PartOutcome:
    """Unchanged from M1's station_s3.py."""

    disposition: str  # "good" | "reject"
    defect_class: str | None
    confidence: float
    image: bytes | None  # §3.4: only rejects carry their image
    # The classifier's own advertised version -- not settings.model_version, which
    # only ever describes the simulator's *expected* model. Once a real
    # ModelClassifier replaces SimulatedClassifier, this field is what still
    # correctly attributes the verdict.
    model_version: str


ProduceFn = Callable[[str, datetime], Awaitable[PartOutcome]]


def serial_for(index: int) -> str:
    """Unchanged from M1. M2b replaces this with serials created at S1."""
    return f"A-{index:08d}"


class StationNodes(Protocol):
    """The address-space handles one station writes through."""

    @property
    def code(self) -> str: ...

    async def write(
        self, signal: str, at: datetime, value: float | int | str
    ) -> None: ...

    async def trigger_event(self, at: datetime, fields: dict[str, object]) -> None: ...


_MAX_TAKT_RESAMPLES = 100
"""Bounds _next_takt's resample loop. A well-formed positive sigma finds a distinct
value on the first or second draw essentially always; this exists so a degenerate
configuration -- takt_jitter_sigma=0, the obvious way someone turns jitter off --
fails loudly instead of spinning forever inside a non-yielding while loop in an async
function, wedging the event loop with no error, timeout, or log."""


class Station(ABC):
    """Four implementations, in this package. That is what justifies the base class
    existing at all (CLAUDE.md)."""

    def __init__(self, nodes: StationNodes, settings: Settings, seed: int) -> None:
        self._nodes = nodes
        self._settings = settings
        # Per-station RNG, derived from the master seed and the station code rather
        # than shared. §3.6's reproducibility must not depend on how many draws a
        # *different* station happened to make first.
        self._rng = random.Random(seed ^ hash(nodes.code) & 0xFFFF)
        self._previous_takt: float | None = None
        self._part_count = 0

    @property
    def code(self) -> str:
        return self._nodes.code

    def next_takt(self) -> float:
        """Additive Gaussian jitter, resampled until distinct from the previous value.

        Needed for TaktTime to be historised at all, not for realism: asyncua's
        monitored-item filter (DataChangeTrigger.StatusValue, the default) drops a
        notification whenever the written value is unchanged, regardless of
        SourceTimestamp. A bare constant takt means only the very first write of a run
        is ever historised. D13 deletes this in M2c, once the noise floor makes takt
        genuinely variable -- at which point the guard is dead code pretending to be a
        safety property.
        """
        # Per station, not one line-wide number (§3.1). Falls back to takt_seconds
        # for a station the configuration does not name -- the same fail-open choice
        # Task 9's signal policy makes, and for the same reason: a station handed a
        # takt of zero because nobody listed it would wedge the queue.
        nominal = self._settings.station_takt_seconds.get(
            self.code, self._settings.takt_seconds
        )
        sigma = self._settings.takt_jitter_sigma
        for _ in range(_MAX_TAKT_RESAMPLES):
            value = nominal + self._rng.gauss(0.0, sigma)
            if value != self._previous_takt:
                self._previous_takt = value
                return value
        raise ValueError(
            f"takt_jitter_sigma={sigma!r} produced {_MAX_TAKT_RESAMPLES} consecutive "
            f"draws equal to the previous takt ({self._previous_takt!r})"
        )

    async def publish_state(self, at: datetime, state: State, reason: str) -> None:
        """§4.1's State and StateReason. Written together and always in this order, so
        a reader that sees Suspended can never read a reason belonging to the state
        before it."""
        await self._nodes.write("State", at, state.value)
        await self._nodes.write("StateReason", at, reason)

    async def run_cycle(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        self._part_count += 1
        # The value written for part i is the interval that will elapse before part
        # i+1, not the interval since part i-1. A query asking "what was the takt at
        # instant T" should look at the next row after T. The reverse convention is
        # equally self-consistent; this is the one this project made.
        await self._nodes.write(
            "TaktTime",
            at,
            self._previous_takt
            or self._settings.station_takt_seconds.get(
                self.code, self._settings.takt_seconds
            ),
        )
        await self._nodes.write("PartCount", at, self._part_count)
        await self.on_part(at, carrier, part)

    @abstractmethod
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        """What this station does to the part in front of it."""
```

- [ ] **Step 4: Write the four stations**

Create `plant/simulator/src/simulator/stations/s1_feeding.py`:

```python
"""S1 Feeding: separates parts from two feeder lanes onto a carrier (§3.1).

Lane lots, component serials and the two events §4.1 gives S1 are M2b. M2a writes the
two lane-fill signals and nothing that would be a placeholder -- §13's standard is
that nothing in a milestone is faked, and a Lot node holding a constant is a fake.
"""

from __future__ import annotations

from datetime import datetime

from simulator.carriers import Carrier
from simulator.line import PartState
from simulator.stations.base import Station


class FeedingStation(Station):
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        # Lane fill is a level that falls as parts are drawn and is topped up by an
        # operator. M2c's scenario 5 contaminates one lane and scenario 1 starves the
        # feed entirely, so both lanes are separate signals from the start.
        for lane in (1, 2):
            level = self._lane_level(lane)
            await self._nodes.write(f"LaneFill_{lane}", at, level)

    def _lane_level(self, lane: int) -> float:
        """A slow sawtooth with noise: drawn down by production, refilled when low.
        The shape carries no diagnosis in M2a -- it exists so the signal is a real
        varying float rather than a constant the historian would coalesce away."""
        drawn = (self._part_count * 0.5) % 100.0
        return round(100.0 - drawn + self._rng.gauss(0.0, 0.4), 3)
```

Create `plant/simulator/src/simulator/stations/s2_joining.py`:

```python
"""S2 Joining: presses the parts together (§3.1).

M2a writes §4.1's two summary signals. §3.4a's force-distance curve -- the thing that
actually separates a press problem from a material problem, and the only thing
separating M2c's scenario 7 from scenario 3 -- is M2b. The two scalars are written
here so the stream exists and the gateway has something to deadband; they are
explicitly not claimed to be sufficient for diagnosis.
"""

from __future__ import annotations

from datetime import datetime

from simulator.carriers import Carrier
from simulator.line import PartState
from simulator.stations.base import Station


class JoiningStation(Station):
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        peak = round(
            self._settings.joining_force_nominal + self._rng.gauss(0.0, 40.0), 2
        )
        distance = round(
            self._settings.joining_distance_nominal + self._rng.gauss(0.0, 0.02), 4
        )
        await self._nodes.write("JoiningForcePeak", at, peak)
        await self._nodes.write("JoiningDistance", at, distance)
```

Create `plant/simulator/src/simulator/stations/s3_inspection.py`:

```python
"""S3 Inspection: the camera captures, the vision system classifies (§3.4).

The inspection call and the event shape are M1's, moved rather than rewritten. The
event keeps M1's EVENT_FIELDS exactly -- D11 widens inspection_results to §5.2's
target shape in M2b, in one ALTER, not here.
"""

from __future__ import annotations

from datetime import datetime

from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.line import PartState
from simulator.stations.base import ProduceFn, Station, StationNodes, serial_for


class InspectionStation(Station):
    def __init__(
        self, nodes: StationNodes, settings: Settings, seed: int, produce: ProduceFn
    ) -> None:
        super().__init__(nodes, settings, seed)
        self._produce = produce

    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        outcome = await self._produce(serial_for(self._part_count - 1), at)
        # S4 sorts on this. Putting it on the part rather than leaving S4 to
        # time-join the event stream is §3.4a's rule applied one station early.
        part.disposition = outcome.disposition
        await self._nodes.trigger_event(
            at,
            {
                "AssemblySerial": serial_for(self._part_count - 1),
                "Disposition": outcome.disposition,
                "DefectClass": outcome.defect_class or "",
                "Confidence": outcome.confidence,
                "ModelVersion": outcome.model_version,
                # §3.4: only rejected parts carry their image.
                "Image": outcome.image or b"",
            },
        )
```

Create `plant/simulator/src/simulator/stations/s4_outfeed.py`:

```python
"""S4 Outfeed: good/bad sorting, and the carrier goes back (§3.1)."""

from __future__ import annotations

from datetime import datetime

from simulator.carriers import Carrier
from simulator.line import PartState
from simulator.stations.base import Station


class OutfeedStation(Station):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._good = 0
        self._reject = 0

    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        if part.disposition is None:
            raise ValueError(
                f"part on carrier {carrier.carrier_id} reached S4 with no disposition: "
                "S3 did not inspect it, and sorting it as good would be exactly the "
                "quiet wrong answer this system exists not to give"
            )
        if part.disposition == "good":
            self._good += 1
        else:
            self._reject += 1

        await self._nodes.write("GoodCount", at, self._good)
        await self._nodes.write("RejectCount", at, self._reject)
        await self._nodes.write("OutfeedFill", at, self._outfeed_level())

    def _outfeed_level(self) -> float:
        """Fills as parts arrive, emptied when an operator clears it. M2c's scenario 2
        blocks the outfeed entirely, which is why this is a level rather than a
        counter."""
        return round((self._good + self._reject) % 50 + self._rng.gauss(0.0, 0.3), 3)
```

The `*args: object` suppression in `OutfeedStation.__init__` is avoidable and should be avoided — write the explicit signature instead:

```python
    def __init__(self, nodes: StationNodes, settings: Settings, seed: int) -> None:
        super().__init__(nodes, settings, seed)
        self._good = 0
        self._reject = 0
```

with `StationNodes` and `Settings` imported. `mypy --strict` with `disallow_any_explicit` will reject the `*args: object` form anyway, and a suppression for a signature you can simply write is the kind CLAUDE.md rules out.

Create `plant/simulator/src/simulator/stations/__init__.py`:

```python
from simulator.stations.base import (
    PartOutcome,
    ProduceFn,
    Station,
    StationNodes,
    serial_for,
)
from simulator.stations.s1_feeding import FeedingStation
from simulator.stations.s2_joining import JoiningStation
from simulator.stations.s3_inspection import InspectionStation
from simulator.stations.s4_outfeed import OutfeedStation

__all__ = [
    "FeedingStation",
    "InspectionStation",
    "JoiningStation",
    "OutfeedStation",
    "PartOutcome",
    "ProduceFn",
    "Station",
    "StationNodes",
    "serial_for",
]
```

- [ ] **Step 5: Add S2's two numbers to configuration**

In `Settings`, beside the line defaults from Task 3:

```python
# §4.1's two S2 process signals. Nominal values only -- M2c's scenario 3 drifts
# the force down from here, and M2b replaces both with the force-distance curve
# they summarise (§3.4a).
joining_force_nominal: float = 4200.0  # newtons
joining_distance_nominal: float = 12.5  # millimetres
```

- [ ] **Step 6: Delete `station_s3.py` and run the tests**

```bash
git rm plant/simulator/src/simulator/station_s3.py
cd plant && uv run --package simulator pytest simulator/tests/test_stations.py -v
```

Expected: PASS, 11 tests. `test_generation.py` and `test_inspection_client.py` will now fail to import — Task 7 rewrites them; leave them failing until then and do **not** commit a red `make check`. If you need an intermediate commit, do Task 5 and Task 7 as one commit.

- [ ] **Step 7: Commit (with Task 7, or after moving the imports)**

Message: `refactor(plant): four stations where there was one, and a base that earns itself`. The body should name the three M1 comments that moved and why each is load-bearing.

---
## Task 6: The address space grows to §4.1

**The count must come out at exactly 25 historised streams**, which is the number every guard in Tasks 9 and 10 is sized against:

| Station | Historised | Count |
|---|---|---|
| S1_Feeding | State · StateReason · TaktTime · LaneFill_1 · LaneFill_2 · PartCount | 6 |
| S2_Joining | State · StateReason · TaktTime · JoiningForcePeak · JoiningDistance · PartCount | 6 |
| S3_Inspection | State · StateReason · TaktTime · PartCount | 4 |
| S4_Outfeed | State · StateReason · TaktTime · OutfeedFill · GoodCount · RejectCount | 6 |
| Buffers ×3 | Level | 3 |
| | **total** | **25** |

Plus 9 static nodes (`Capacity`, `UpstreamStation`, `DownstreamStation` per buffer), read on connect and never historised. **`Lane1_Lot`, `Lane2_Lot` and `CurrentAssemblySerial` are not created at all in M2a** — they are M2b's, and M1 set the precedent that a node holding a value that never changes is a fake, which §13's standard rules out.

**A deviation from §4.1's wording, recorded rather than absorbed.** §4.1 says buffers *reference* the stations they sit between. This implements `UpstreamStation`/`DownstreamStation` as `String` variables holding the station's browse name, not as a custom OPC UA reference type. The topology is equally discovered either way — the gateway browses and reads rather than being configured — and a custom hierarchical reference type adds asyncua/UA-.NETStandard interop risk for no diagnostic gain. **Raise this with the spec owner; if accepted, §4.1's wording should say "carry" rather than "reference".**

**Files:**
- Modify: `plant/simulator/src/simulator/address_space.py`, `plant/simulator/tests/test_address_space.py`

**Interfaces:**
- Produces: `AddressSpace` with `.idx`, `.line`, `.clock_time/.clock_phase/.clock_speed`, `.stations: dict[str, StationNodeSet]`, `.buffers: dict[str, BufferNodeSet]`; `StationNodeSet` implements Task 5's `StationNodes` Protocol (`.code`, `.write`, `.trigger_event`). Task 7 consumes all of it.

- [ ] **Step 1: Extend the address-space test**

In `plant/simulator/tests/test_address_space.py`, replace `test_no_packml_state_in_m1` (the test asserting `State`/`StateReason` are absent — it has served its purpose and is now false) with:

```python
@pytest.mark.asyncio
async def test_the_tree_carries_exactly_twenty_five_historised_streams(
    space: AddressSpace,
) -> None:
    """The number Tasks 9 and 10's guards are sized against. §15 said nine; §4.1's
    own tree says this. If it moves, the page sizes and the truncation guard move
    with it."""
    streams = sum(len(nodes.historised) for nodes in space.stations.values())
    streams += sum(1 for _ in space.buffers)  # Level only
    assert streams == 25


@pytest.mark.asyncio
async def test_every_station_carries_packml_state_and_its_reason(
    space: AddressSpace,
) -> None:
    """§4.1 gives all four both. M1 omitted them deliberately, because a State node
    that never leaves Execute is a fake; the state machine arrived in Task 2, so they
    are real now."""
    for code, nodes in space.stations.items():
        assert "State" in nodes.historised, code
        assert "StateReason" in nodes.historised, code


@pytest.mark.asyncio
async def test_buffers_name_the_stations_they_sit_between(space: AddressSpace) -> None:
    """§4.1: the gateway reads these on connect and fills the buffers table, so the
    topology is discovered rather than configured."""
    between = {
        buffer_id: (
            await nodes.upstream.read_value(),
            await nodes.downstream.read_value(),
        )
        for buffer_id, nodes in space.buffers.items()
    }
    assert between == {
        "B1_2": ("S1_Feeding", "S2_Joining"),
        "B2_3": ("S2_Joining", "S3_Inspection"),
        "B3_4": ("S3_Inspection", "S4_Outfeed"),
    }


@pytest.mark.asyncio
async def test_m2b_nodes_are_absent_rather_than_holding_constants(
    space: AddressSpace,
) -> None:
    """§13's standard: nothing in a milestone is faked. Lots and assembly serials
    arrive in M2b with the data that makes them change."""
    s1 = space.stations["S1_Feeding"]
    assert "Lane1_Lot" not in s1.historised
    assert "CurrentAssemblySerial" not in s1.historised
```

- [ ] **Step 2: Run to verify the new ones fail**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_address_space.py -v
```

- [ ] **Step 3: Rewrite `address_space.py`**

Keep `Clock` exactly as M1 built it — including its comment on why it is never historised, which is still the reason R1's reconciliation count works. Replace the single-station body with a table-driven build:

```python
# §4.1's tree, as data rather than as four near-identical code paths. Adding a fifth
# station is a row here and nothing else, which is the same claim §4.1 makes about the
# gateway: topology is described in one place, never spread across code.
STATION_SIGNALS: dict[str, tuple[tuple[str, ua.VariantType], ...]] = {
    "S1_Feeding": (
        ("TaktTime", ua.VariantType.Double),
        ("LaneFill_1", ua.VariantType.Double),
        ("LaneFill_2", ua.VariantType.Double),
        ("PartCount", ua.VariantType.UInt32),
    ),
    "S2_Joining": (
        ("TaktTime", ua.VariantType.Double),
        ("JoiningForcePeak", ua.VariantType.Double),
        ("JoiningDistance", ua.VariantType.Double),
        ("PartCount", ua.VariantType.UInt32),
    ),
    "S3_Inspection": (
        ("TaktTime", ua.VariantType.Double),
        ("PartCount", ua.VariantType.UInt32),
    ),
    "S4_Outfeed": (
        ("TaktTime", ua.VariantType.Double),
        ("OutfeedFill", ua.VariantType.Double),
        ("GoodCount", ua.VariantType.UInt32),
        ("RejectCount", ua.VariantType.UInt32),
    ),
}

# Every station carries both (§4.1). Separate from the table above because they are
# the same for all four and because their VariantType is what makes them un-deadbandable
# on the gateway side (Task 9's policy defaults).
PACKML_SIGNALS: tuple[tuple[str, ua.VariantType], ...] = (
    ("State", ua.VariantType.String),
    ("StateReason", ua.VariantType.String),
)

BUFFERS: tuple[tuple[str, str, str], ...] = (
    ("B1_2", "S1_Feeding", "S2_Joining"),
    ("B2_3", "S2_Joining", "S3_Inspection"),
    ("B3_4", "S3_Inspection", "S4_Outfeed"),
)
```

`StationNodeSet` holds `code`, `node`, `historised: dict[str, Node]`, and (for S3 only) `event_gen`. Its `write()` resolves the signal name to a node and performs M1's `write_value` with the `SourceTimestamp` suppression **in this one place**:

```python
    async def write(self, signal: str, at: datetime, value: float | int | str) -> None:
        node = self.historised[signal]
        variant_type = self.types[signal]
        # SourceTimestamp= carries a suppression for the reason M1 measured and wrote
        # down: DataValue types the field as ua.DateTime, a datetime subclass asyncua's
        # own runtime never constructs. sqlite3's parameter binder matches by exact
        # type since 3.12 deprecated the implicit subclass adapter, so a real
        # ua.DateTime raises "type 'DateTime' is not supported" *inside*
        # HistorySQLite.save_node_value's own try/except -- silently logged, not
        # raised, and every row goes missing with no visible error. A plain datetime is
        # what the storage layer needs.
        await node.write_value(
            ua.DataValue(
                ua.Variant(value, variant_type),
                SourceTimestamp=at,  # type: ignore[arg-type]
            )
        )
```

`BufferNodeSet` holds `level`, `capacity`, `upstream`, `downstream`. Only `level` is historised; the other three are written once at build time.

The event type keeps M1's `EVENT_FIELDS` and M1's ORDER MATTERS comment verbatim — `get_event_generator` must run before `historize_node_event`, or event history is created with no columns.

- [ ] **Step 4: Run the suite**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_address_space.py -v
```

Expected: PASS. The 25 must be exact — if it is 26 or 24, the tree and the table above disagree and one of them is wrong.

- [ ] **Step 5: Commit** — `feat(plant): §4.1's tree, four stations and three buffers`, body naming the 25/9/3 split and the `UpstreamStation` deviation to be raised.

---

## Task 7: Catch-up and live, across twenty-five streams

M1's `generate_history` and `run_live` move into `line.py` and become queue-driven. The reconciliation they perform is the same claim at nine times the width, and it is R1's pass condition: **the historian's row count must equal the ledger, exactly, per stream.** A ledger of what `write_value` was *called* for is not the same claim, and the queue-cap defect M1 found is exactly the gap between them.

**Files:**
- Modify: `plant/simulator/src/simulator/line.py`, `historian.py`, `server.py`, `status.py`
- Modify: `plant/simulator/tests/test_generation.py`, `test_inspection_client.py`
- **Delete: `plant/simulator/src/simulator/station_s3.py`** — see below

### The deletion this task owes

Task 5 created `stations/` but was ruled to **leave `station_s3.py` in place**, so that every
task ends with a green gate and its own reviewable commit. The cost is duplication that
CLAUDE.md forbids as a steady state, and **this task is what pays it**. Not optional.

Living in both places until this task removes the originals:

| Duplicated | In `station_s3.py` | Now also in |
|---|---|---|
| `PartOutcome`, `ProduceFn`, `serial_for` | top of file | `stations/base.py` |
| `_MAX_TAKT_RESAMPLES` and the takt-jitter resample loop | `_next_takt` | `Station.next_takt` |
| The six-field event construction with its `or ""` / `or b""` fallbacks | `_emit_part` | `stations/s3_inspection.py` |
| The forward-looking-takt convention comment | `generate_history` docstring | `Station.run_cycle` |

**`inspection_client.py:13` imports `station_s3.PartOutcome` and must be repointed** to
`stations.base.PartOutcome`. These are two distinct classes, so `mypy --strict` refuses the
wiring the moment this task attempts it — the duplication cannot go silently wrong, only
permanently un-deleted.

**One ordering hazard to preserve while moving code.** `Station.run_cycle` reads
`self._previous_takt` rather than being handed the takt, so `TaktTime` is the *current*
cycle's interval only because `line.py` calls `next_takt()` **before** `run_cycle()`. Swap
those two calls and `TaktTime` silently becomes the previous cycle's interval — a wrong
number in the historian with no test failing.

**Interfaces:**
- Produces: `Ledger` keyed per `(station_code, signal)` rather than M1's three named fields; `run_catchup(line, clock, settings, storage) -> datetime`; `run_live(line, clock, settings, start_ts) -> None`.

- [ ] **Step 1: Widen the ledger**

`historian.py`'s `Ledger` currently has `takt`, `part_count`, `events`. Replace with:

```python
@dataclass
class Ledger:
    """What the generator believes it wrote, per stream. Compared against the
    historian's own row counts before catch-up returns -- the two are different
    claims, and R1 needs the second one."""

    rows: dict[tuple[str, str], int] = field(default_factory=dict)
    events: int = 0
    images: int = 0
    image_bytes: int = 0

    def record(self, station_code: str, signal: str) -> None:
        key = (station_code, signal)
        self.rows[key] = self.rows.get(key, 0) + 1
```

- [ ] **Step 2: Write the catch-up driver**

In `line.py`:

```python
async def run_catchup(
    line: Line, clock: SimulatedClock, settings: Settings, storage: HistorySQLite
) -> datetime:
    """Build the configured depth of history in process (§3.2), returning the
    simulated instant production should resume at.

    Returned rather than recomputed from `history_start + history_depth`: that would
    be wrong by the cumulative takt jitter, which at 33 h is several seconds and would
    leave a hole nothing above can see.

    Paced in batches: asyncua's per-monitored-item notification queue caps at 10,000
    and silently discards the *oldest* past that -- M1 measured 19,800 parts losing
    the first 9,800 rows per stream with no pacing at all. Task 1 (R5) re-measured
    the batch size that holds at twenty-five streams; that number is
    `settings.catchup_batch_size` and is not to be guessed.
    """
    line.seed(clock.history_start)
    horizon = clock.history_start + clock.history_depth
    cycles = 0
    latest = clock.history_start

    while True:
        outcome = await line.step()
        if outcome is None or outcome.at >= horizon:
            break
        latest = outcome.at
        cycles += 1
        if cycles % settings.catchup_batch_size == 0:
            await asyncio.sleep(settings.catchup_batch_pause_seconds)

    await reconcile_with_historian(line, storage, settings)
    return latest
```

- [ ] **Step 3: Widen the reconciliation**

M1's `_reconcile_with_historian` polls the historian's row counts until they match the ledger or a timeout elapses. Keep the mechanism and its docstring reasoning verbatim; widen it from three hardcoded streams to every `(station, signal)` key in the ledger, plus the event stream. The error message must name **which** streams disagree and by how much — at 25 streams, "historian disagrees with the ledger" without the offender is a message that costs an hour.

```python
        if counts != expected:
            differing = {
                key: (counts.get(key, 0), expected[key])
                for key in expected
                if counts.get(key, 0) != expected[key]
            }
            raise RuntimeError(
                f"historian disagrees with the ledger on {len(differing)} of "
                f"{len(expected)} streams after settling: "
                + ", ".join(
                    f"{station}.{signal} historian={got} ledger={want}"
                    for (station, signal), (got, want) in sorted(differing.items())
                )
            )
```

- [ ] **Step 4: Write the live driver**

`run_live` keeps M1's cursor discipline exactly, and the reasoning must travel with it: advance the simulated cursor by the interval just written and sleep until that instant arrives, rather than stamping each part with `clock.now()`. The two differ by precisely the hole catch-up's wall time would otherwise leave in the simulated timeline. Its RNG stream is `settings.seed ^ 1`, so changing the history depth cannot shift what live produces.

```python
async def run_live(
    line: Line, clock: SimulatedClock, settings: Settings, start_ts: datetime
) -> None:
    line.seed(start_ts)
    while True:
        if clock.phase is not Phase.LIVE:
            await asyncio.sleep(settings.takt_seconds)
            continue
        outcome = await line.step()
        if outcome is None:
            return
        await asyncio.sleep(max(0.0, (outcome.next_at - clock.now()).total_seconds()))
```

- [ ] **Step 5: Rewire `server.py`**

`server.main` builds the `AddressSpace`, then four stations bound to their `StationNodeSet`s, three `Buffer`s from `settings.buffer_capacity`, a `CarrierPool(settings.carrier_count)`, and a `Line`. `publish_clock` and `simulator.status.publish` stay in the same TaskGroup, on the same cadence. `simulator.status` gains per-station state and per-buffer level, because that snapshot is what `make m2a-demo` prints and what Task 11's HMI falls back to.

- [ ] **Step 6: Update the generation tests**

`test_generation.py` asserts the row-count equality at the new width. Its existing `test_source_timestamps_are_simulated_not_wall_clock` stays as written — it is the `SourceTimestamp` invariant and does not change. Add:

```python
@pytest.mark.asyncio
async def test_every_stream_reconciles_exactly(...) -> None:
    """R1's criterion, at twenty-five streams instead of three. Not 'approximately':
    the three defects M1 found all present as a count that is short and a run that
    reports success."""
```

- [ ] **Step 7: Run the full plant suite and the gate**

```bash
cd plant && uv run --package simulator pytest -v
make check
```

- [ ] **Step 8: Commit** — `feat(plant): catch-up and live on the queue, reconciled per stream`. Body: the ledger is now per-stream because a single total hides which stream lost rows, and R5's measured batch size, named.

---
## Task 8: Migration 002, and the two streams that are not numbers

`signals.value` is `DOUBLE PRECISION`. `State` and `StateReason` are strings and cannot go there; buffer `Level` belongs to a buffer, not a station. Both need their own tables, and §5.2 already names them.

**The pairing trick that makes `state_changes` one row.** Task 5 writes `State` and then `StateReason` at the *same* `SourceTimestamp`. They arrive as two separate data changes. Keying `state_changes` on `(station_id, source_ts)` and upserting lets the first write insert `to_state` and the second fill `reason` — one row, no buffering in the gateway, and no dependence on which arrives first.

**`from_state` requires memory.** A data change carries only the new value. The gateway keeps the last state per station and writes `from_state` from it; the first observation after a connect writes `NULL`, which is honest — it did not see the transition.

**Files:**
- Create: `diagnostics/gateway/Gateway/Migrations/002_m2a.sql`
- Modify: `diagnostics/gateway/Gateway/Ingest/PostgresWriter.cs`, `Gateway.Tests/PostgresWriterTests.cs`

- [ ] **Step 1: Write the failing writer tests**

In `PostgresWriterTests.cs`, add:

```csharp
[Fact]
public async Task StateAndItsReasonBecomeOneRow()
{
    // They arrive as two data changes sharing a SourceTimestamp, because that is how
    // the station writes them. One transition must not become two rows.
    var ts = new DateTime(2026, 9, 13, 6, 0, 0, DateTimeKind.Utc);
    await writer.WriteBatchAsync([
        DataChange("S2_Joining", "State", ts, "Suspended"),
        DataChange("S2_Joining", "StateReason", ts, "starved:B1_2"),
    ]);

    var rows = await Query("SELECT to_state, reason FROM state_changes");
    Assert.Single(rows);
    Assert.Equal("Suspended", rows[0]["to_state"]);
    Assert.Equal("starved:B1_2", rows[0]["reason"]);
}

[Fact]
public async Task TheSuspendReasonResolvesToTheBufferItNames()
{
    // §3.3 makes this field non-optional because it is what turns propagation from
    // inferred into verifiable. A reason stored only as text would leave every
    // propagation query doing string surgery.
    await SeedTopology();
    await writer.WriteBatchAsync([
        DataChange("S3_Inspection", "State", Ts, "Suspended"),
        DataChange("S3_Inspection", "StateReason", Ts, "starved:B2_3"),
    ]);

    var rows = await Query(
        "SELECT b.code FROM state_changes s JOIN buffers b ON b.id = s.reason_buffer_id");
    Assert.Equal("B2_3", rows[0]["code"]);
}

[Fact]
public async Task AReasonNamingSomethingThatIsNotABufferStillStores()
{
    // "starved:carrier-return" is a real condition with no buffer behind it. The row
    // must survive with a null reason_buffer_id rather than being dropped.
    await writer.WriteBatchAsync([
        DataChange("S1_Feeding", "State", Ts, "Suspended"),
        DataChange("S1_Feeding", "StateReason", Ts, "starved:carrier-return"),
    ]);
    var rows = await Query("SELECT reason, reason_buffer_id FROM state_changes");
    Assert.Equal("starved:carrier-return", rows[0]["reason"]);
    Assert.Equal(DBNull.Value, rows[0]["reason_buffer_id"]);
}

[Fact]
public async Task TheFirstStateSeenAfterAConnectHasNoPredecessor()
{
    await writer.WriteBatchAsync([DataChange("S1_Feeding", "State", Ts, "Execute")]);
    var rows = await Query("SELECT from_state FROM state_changes");
    Assert.Equal(DBNull.Value, rows[0]["from_state"]);
}

[Fact]
public async Task BufferLevelsDoNotLandInTheSignalsTable()
{
    // signals is keyed on a station. A buffer level keyed to a station would have to
    // pick one of the two it sits between, and either choice is wrong.
    await SeedTopology();
    await writer.WriteBatchAsync([BufferLevel("B2_3", Ts, 3)]);
    Assert.Empty(await Query("SELECT 1 FROM signals"));
    Assert.Single(await Query("SELECT 1 FROM buffer_levels"));
}
```

- [ ] **Step 2: Run to verify they fail**

```bash
cd diagnostics/gateway && dotnet test --filter FullyQualifiedName~PostgresWriterTests
```

- [ ] **Step 3: Write migration 002**

Create `diagnostics/gateway/Gateway/Migrations/002_m2a.sql`:

```sql
-- M2a's slice of §5.2. Plain PostgreSQL with BRIN indexes on the time columns, as
-- 001 established; TimescaleDB adds operational complexity this volume does not
-- justify.

CREATE TABLE IF NOT EXISTS buffers (
  id                    SMALLSERIAL PRIMARY KEY,
  code                  TEXT     NOT NULL UNIQUE,   -- discovered by browsing (§4.1)
  upstream_station_id   SMALLINT NOT NULL REFERENCES stations(id),
  downstream_station_id SMALLINT NOT NULL REFERENCES stations(id),
  capacity              SMALLINT NOT NULL
);

-- Declared, not discovered: §4.1 exposes no carrier nodes. The pool size is a plant
-- configuration value the gateway learns from the carriers it observes, so this table
-- fills as carrier ids are seen rather than at connect. Empty in M2a beyond that --
-- M2b's genealogy is what gives a carrier anything to be joined to.
CREATE TABLE IF NOT EXISTS carriers (
  id SMALLINT PRIMARY KEY
);

-- §3.3: every Suspended transition records which buffer and which direction. The
-- reason is kept verbatim *and* resolved, because "starved:carrier-return" names a
-- real condition with no buffer behind it and must still store.
CREATE TABLE IF NOT EXISTS state_changes (
  station_id       SMALLINT    NOT NULL REFERENCES stations(id),
  source_ts        TIMESTAMPTZ NOT NULL,
  from_state       TEXT,                 -- null on the first state seen after a connect
  to_state         TEXT        NOT NULL,
  reason           TEXT,
  reason_buffer_id SMALLINT    REFERENCES buffers(id),
  -- State and StateReason arrive as two data changes sharing one SourceTimestamp.
  -- This key is what makes them one row rather than two.
  PRIMARY KEY (station_id, source_ts)
);
CREATE INDEX IF NOT EXISTS state_changes_source_ts_brin
  ON state_changes USING brin (source_ts);

CREATE TABLE IF NOT EXISTS buffer_levels (
  buffer_id SMALLINT    NOT NULL REFERENCES buffers(id),
  source_ts TIMESTAMPTZ NOT NULL,
  level     SMALLINT    NOT NULL,
  PRIMARY KEY (buffer_id, source_ts)
);
CREATE INDEX IF NOT EXISTS buffer_levels_source_ts_brin
  ON buffer_levels USING brin (source_ts);
```

- [ ] **Step 4: Route the new kinds in `PostgresWriter`**

`ApplySchemaAsync` runs both migrations in order — replace the single `MigrationResource` constant with an ordered array, so a fresh database gets `001` then `002` and an existing one is unchanged by the `IF NOT EXISTS` clauses.

In `WriteBatchAsync`'s `"datachange"` branch, route by signal name before falling through to `signals`:

```csharp
    "datachange" => Required(payload, "Signal").GetString() switch
    {
        // State and StateReason are strings; signals.value is DOUBLE PRECISION. They
        // are not a special case of a numeric signal, they are a different stream.
        "State" or "StateReason" =>
            await UpsertStateChangeAsync(connection, record, payload, stationId, ct)
                .ConfigureAwait(false),
        // A buffer level belongs to a buffer, not to either station it sits between.
        "Level" => await UpsertBufferLevelAsync(connection, record, payload, ct)
            .ConfigureAwait(false),
        _ => await UpsertSignalAsync(connection, record, payload, stationId, ct)
            .ConfigureAwait(false),
    },
```

`UpsertStateChangeAsync` writes with `ON CONFLICT (station_id, source_ts) DO UPDATE`, setting only the column its own signal carries (`COALESCE(EXCLUDED.to_state, state_changes.to_state)` for the same reason). `from_state` comes from a `ConcurrentDictionary<string, string>` of last-seen state per station code, mirroring `_stationIds`. The reason's buffer is resolved with a `SELECT id FROM buffers WHERE code = $1` on the substring after `:`, and a miss stores `NULL` rather than failing.

- [ ] **Step 5: Run the tests, then the gate**

```bash
cd diagnostics/gateway && dotnet test --filter FullyQualifiedName~PostgresWriterTests
make check
```

- [ ] **Step 6: Commit** — `feat(gateway): state changes and buffer levels, and the pairing that keeps one transition one row`.

---

## Task 9: Topology discovery grows, and deadbands stop being hardcoded

Two assumptions M1 baked in break here. `GatewayOptions.TaktDeadband` is a single scalar and `Subscriptions.cs` hardcodes which signal receives it; M2a has 25 streams across three kinds. D3 replaces both with a mounted policy file that **fails open**.

**Fail-open is the whole point.** Topology is discovered, so the gateway will meet signals no policy names. A policy that skipped an unknown signal would lose a stream and pass every test. The default for an unknown signal is: subscribe, no deadband, record everything.

**Files:**
- Create: `diagnostics/gateway/Gateway/Opc/SignalPolicy.cs`, `Gateway/config/signals.json`, `Gateway.Tests/SignalPolicyTests.cs`
- Modify: `Opc/TopologyDiscovery.cs`, `Opc/AddressSpace.cs`, `Opc/Subscriptions.cs`, `Opc/GatewayOptions.cs`, `diagnostics/compose.yml`

- [ ] **Step 1: Write the failing policy tests**

```csharp
[Fact]
public void AnUnknownSignalIsSubscribedWithNoDeadband()
{
    // The case that matters. Topology is discovered, so this will happen -- and a
    // policy that failed closed would lose a whole stream silently, which is the
    // exact failure shape M1 spent four risks measuring.
    var policy = SignalPolicy.Parse(MinimalJson);
    var rule = policy.For("SomeSignalNobodyPlanned", BuiltInType.Double);
    Assert.True(rule.Subscribe);
    Assert.Null(rule.Deadband);
}

[Fact]
public void CountersNeverGetADeadband()
{
    // §5.1's lesson from M1: a deadband on a monotonic counter silently loses parts.
    var policy = SignalPolicy.Parse(RealJson);
    foreach (var counter in new[] { "PartCount", "GoodCount", "RejectCount" })
    {
        Assert.Null(policy.For(counter, BuiltInType.UInt32).Deadband);
    }
}

[Fact]
public void BufferLevelGetsNoDeadbandEitherBecauseItMovesByOne()
{
    // A deadband of 0.5 on a level that steps by 1 would swallow half the
    // transitions, and Task 12's propagation proof watches B2_3 drain one at a time.
    Assert.Null(SignalPolicy.Parse(RealJson).For("Level", BuiltInType.UInt16).Deadband);
}

[Fact]
public void StateAndItsReasonAreNeverDeadbanded()
{
    // A deadband on a string is meaningless, and every transition matters.
    var policy = SignalPolicy.Parse(RealJson);
    Assert.Null(policy.For("State", BuiltInType.String).Deadband);
    Assert.Null(policy.For("StateReason", BuiltInType.String).Deadband);
}

[Fact]
public void NoisyFloatsKeepTheirConfiguredDeadband()
{
    Assert.Equal(0.05, SignalPolicy.Parse(RealJson).For("TaktTime", BuiltInType.Double).Deadband);
}

[Fact]
public void AMalformedPolicyFileFailsLoudlyRatherThanSilentlyDefaulting()
{
    // Falling back to "no policy" here would be indistinguishable from a policy that
    // happened to name nothing, and the operator would never learn the file is wrong.
    Assert.Throws<JsonException>(() => SignalPolicy.Parse("{ not json"));
}
```

- [ ] **Step 2: Write the policy file**

Create `diagnostics/gateway/Gateway/config/signals.json`:

```json
{
  "_comment": "Per-signal ingest policy (§5.1, M2 design D3). An unrecognised signal is subscribed with no deadband: topology is discovered, so this file will always be incomplete, and losing a stream is worse than storing a few redundant rows.",
  "defaults": { "page_size": 1000 },
  "signals": {
    "TaktTime":         { "deadband": 0.05,  "why": "noisy float; 0.05 s is under the jitter sigma" },
    "JoiningForcePeak": { "deadband": 5.0,   "why": "newtons, against a 4200 N nominal and 40 N sigma" },
    "JoiningDistance":  { "deadband": 0.005, "why": "millimetres, against a 0.02 mm sigma" },
    "LaneFill_1":       { "deadband": 0.5,   "why": "a slow level; sub-unit noise is not a change" },
    "LaneFill_2":       { "deadband": 0.5,   "why": "as LaneFill_1" },
    "OutfeedFill":      { "deadband": 0.5,   "why": "as LaneFill_1" },
    "Level":            { "deadband": null,  "why": "steps by one; any deadband swallows transitions" },
    "PartCount":        { "deadband": null,  "why": "monotonic counter; a deadband loses parts" },
    "GoodCount":        { "deadband": null,  "why": "as PartCount" },
    "RejectCount":      { "deadband": null,  "why": "as PartCount" },
    "State":            { "deadband": null,  "why": "string; every transition matters" },
    "StateReason":      { "deadband": null,  "why": "string; paired with State at one timestamp" }
  }
}
```

Each entry may also carry `page_size`, which Task 10 reads for backfill. Omitted means `defaults.page_size` (1,000, matching `GatewayOptions.HistoryPageSize`). The **event** stream is deliberately not in this file — it keeps `HistoryEventPageSize = 25`, because its page size is forced by image bytes against a 4 MiB response limit rather than by anything the signal itself is.

*Ruling recorded at pre-flight: D3 specifies the policy as `{deadband, page_size}` and this file initially carried only `deadband`. Page size lives here rather than in a second mechanism, because splitting one signal's behaviour across two files is how the two drift.*

**Interfaces produced by this task:** `SignalPolicy.Parse(string json) -> SignalPolicy`, `SignalPolicy.For(string signal, BuiltInType type) -> SignalRule`, and `SignalRule(bool Subscribe, double? Deadband, int PageSize)`. Task 10 consumes `SignalRule.PageSize`.

Mount it read-only in `diagnostics/compose.yml` on `edge-gateway`, and add `GATEWAY_SIGNAL_POLICY` to `GatewayOptions` (default `/config/signals.json`). A missing file is a start-up failure, not a silent default — an operator who mounted it wrong must find out at boot.

- [ ] **Step 3: Discover buffers and fill `position_in_line`**

`TopologyDiscovery` gains `DiscoveredBuffer(string Code, NodeId LevelNodeId, string Upstream, string Downstream, int Capacity)`, browsing `Line/Buffers` and reading the four child variables. `UpsertAsync` writes them, resolving the station codes through the same `stations` rows it just wrote.

`position_in_line` — left null in M1 because "it is derivable from buffer references, and buffers arrive in M2" — is filled here: the station that is no buffer's `downstream` is position 1, and each buffer's `downstream` is one past its `upstream`. A line whose buffers do not form a single chain is a topology this gateway cannot order, and it must say so rather than write an arbitrary order.

`AddressSpace.ResolveAsync` stops hardcoding three browse paths and resolves every discovered station's every discovered child variable, plus each buffer's `Level`.

- [ ] **Step 4: Rebuild `Subscriptions.StartAsync` from the discovered set**

Replace the three hand-built `MonitoredItem`s with a loop over the discovered signals, each item's `Filter` coming from `SignalPolicy.For(...)`. Keep M1's `DiscardOldest = false` for counters — *prefer failing loudly over dropping a count* — and `true` for deadbanded floats. The event item is unchanged in M2a: M1's `InspectionEventFields` and `BuildInspectionFilter()` stay exactly as they are.

Keep the `_lastSourceTs` overflow bookkeeping and widen it to every stream; it is what turns "some were lost" into the interval §4.4 asks a gap marker to name.

- [ ] **Step 5: Run the gateway tests and the gate**

```bash
cd diagnostics/gateway && dotnet test
make check
```

- [ ] **Step 6: Commit** — `feat(gateway): a signal policy that fails open, and buffers in the topology it discovers`. Body: why fail-open (a discovered topology guarantees unknown signals; a policy that skips one loses a stream and passes every test), and why `Level` and the counters carry no deadband.

---

## Task 10: Backfill at twenty-five streams, and a page size per stream

§12 says M2 compounds M1's three truncation defects. This is where that is either handled or discovered in production.

`HistoryEventPageSize = 25` exists solely because S3's inspection events carry images against a 4 MiB response limit — M1 measured a reject image at up to 110,486 B. M2a adds no new event types (they are M2b's), so the 25 stays correct for the one event stream. What changes is **variables**: 25 streams instead of 2, each backfilled per window.

**Files:**
- Modify: `Opc/HistoryBackfill.cs`, `Opc/GatewayOptions.cs`, `Gateway.Tests/` (new backfill tests)

**Page size comes from Task 9's policy file** — `SignalPolicy.For(signal, type).PageSize`, defaulting to `defaults.page_size` — not from a second mechanism. The event stream is the exception and keeps `HistoryEventPageSize = 25`.

- [ ] **Step 1: Write the failing tests**

```csharp
[Fact]
public void AFullFinalPageWithNoContinuationPointIsTreatedAsTruncated()
{
    // M1's defect, restated at the new width. A client paging smaller than the
    // server's cap receives one page and no continuation point, and stops early
    // believing the window complete -- 96% of event history lost on a green run.
    var result = HistoryBackfill.ClassifyPage(rowsReturned: 1000, pageSize: 1000, hasContinuation: false);
    Assert.Equal(PageVerdict.Truncated, result);
}

[Fact]
public void AShortFinalPageWithNoContinuationPointIsComplete()
{
    Assert.Equal(
        PageVerdict.Complete,
        HistoryBackfill.ClassifyPage(rowsReturned: 431, pageSize: 1000, hasContinuation: false));
}

[Fact]
public void AWindowThatKeepsComingBackFullIsSubdividedUntilItDoesNot()
{
    // Halving, not retrying: a retry at the same width returns the same truncated
    // page forever.
    var windows = HistoryBackfill.Subdivide(
        from: Ts, to: Ts.AddHours(1), floor: TimeSpan.FromSeconds(30));
    Assert.Equal(2, windows.Count);
    Assert.Equal(TimeSpan.FromMinutes(30), windows[0].To - windows[0].From);
    Assert.Equal(windows[0].To, windows[1].From);
}

[Fact]
public void SubdividingPastTheFloorFailsRatherThanLosingRows()
{
    // MinimumBackfillWindow is 30 s, ~5 parts at a 6 s takt. Reaching it means
    // something other than volume is wrong, and the run must fail rather than
    // quietly store a short window.
    var exception = Assert.Throws<InvalidOperationException>(() =>
        HistoryBackfill.Subdivide(
            from: Ts, to: Ts.AddSeconds(30), floor: TimeSpan.FromSeconds(30)));
    Assert.Contains("floor", exception.Message, StringComparison.Ordinal);
}

[Fact]
public async Task EveryStreamGetsItsOwnReconciliationRow()
{
    // R1's ledger is per stream. One aggregate row across 25 streams cannot say
    // which one came back short, which is the only thing the ledger is for.
    // Station CODE, not browse name. "two codes for one station means two rows" is the
    // rule Task 9 enforced at the buffer boundary, and the ledger must not reintroduce a
    // second station identifier into the schema.
    foreach (var stream in new[] { "S2.TaktTime", "S2.JoiningForcePeak" })
    {
        await writer.RecordBackfillWindowAsync(
            Ts, Ts.AddHours(1), stream, rowsReturned: 600, pages: 1, durationMs: 12);
    }

    var rows = await Query("SELECT stream, rows_returned FROM backfill_windows ORDER BY stream");
    Assert.Equal(2, rows.Count);
    Assert.Equal("S2.JoiningForcePeak", rows[0]["stream"]);
    Assert.Equal("S2.TaktTime", rows[1]["stream"]);
}
```

- [ ] **Step 2: Verify the guard covers every discovered variable stream**

The existing guard lives in the event path. Confirm by test — not by reading — that it also runs for variable streams: `read_node_history` has the identical structure, so variables are safe only when the page size happens to equal the server's cap. Widen `ClassifyPage` to a shared helper both paths call.

- [ ] **Step 3: Give the reconciliation ledger a row per stream**

`backfill_windows` is already keyed `(from_ts, to_ts, stream)`, so this is a caller change: emit one `RecordBackfillWindowAsync` per stream per window rather than one per window.

- [ ] **Step 4: Run a real backfill against the plant and check the totals**

```bash
docker compose -f plant/compose.yml up -d --build
docker compose -f diagnostics/compose.yml up -d --build
watch -n1 'curl -s localhost:${GATEWAY_PORT:-8080}/reconcile | jq'
```

Expected: `read_rows == pg_rows` for all 25 streams and the event stream, `ingest_gaps` empty. **This is R1's criterion and it is not waivable** — a short stream here is the defect M1 found, reproduced at the scale §12 predicted.

- [ ] **Step 5: Commit** — `fix(gateway): the truncation guard covers variables too, and the ledger names the stream`.

---
## Task 11: The thin HMI — the screen you build the plant with

D5: the WebSocket is served **in-process by the simulator**. FastAPI and uvicorn are already resolved in the plant workspace by the inspection service, so no new version enters the dependency graph — but they are new to the `simulator` package and **the commit message must justify them** (CLAUDE.md). In-process means no second source of truth, and M2c's fault injection reaches the scenario engine directly rather than needing its own channel back in.

`plant-hmi` joins **`plant-net`**. Never `field-net` — `test_compose_invariants.py` already asserts exactly two members there and will fail if this is got wrong.

**Scope is deliberately thin:** four stations coloured by state, three buffer fill bars, a clock panel. The part strip is M2b; alarms and fault injection are M2c. §15 defers visual language until M6, so this is undesigned on purpose, exactly as the M1 chat box is.

**§15's banked idea, adopted now.** §15 files "colour by category rather than by state — producing, waiting-on-others, held-by-own-fault, stopped, transitioning" against M6. It appears to have been filed against the wrong frontend: the plant HMI is the one with PackML states, and this is the distinction the entire project exists to make. It costs nothing to adopt here. **Colour is never the only channel** (ISA-101, and the obvious accessibility reason) — every station shows its state name as text beside the colour.

| Category | PackML states | Reads as |
|---|---|---|
| producing | `Execute` | running |
| waiting-on-others | `Suspended`, `Suspending`, `Unsuspending` | **consequence** — someone else's problem |
| held-by-own-fault | `Held`, `Holding`, `Unholding`, `Aborted`, `Aborting` | **cause candidate** — this station |
| stopped | `Stopped`, `Stopping`, `Idle` | not running, no fault |
| transitioning | `Clearing`, `Resetting`, `Starting` | coming up |

**Files:**
- Create: `plant/simulator/src/simulator/hmi.py`, `plant/simulator/tests/test_hmi.py`
- Create: `plant/hmi/` (package.json, vite.config.ts, index.html, Dockerfile, nginx.conf, `src/`)
- Modify: `plant/simulator/pyproject.toml`, `plant/compose.yml`, `Makefile`

- [ ] **Step 1: Write the failing snapshot test**

```python
"""The HMI's payload. Asserted as a contract -- the frontend reads these names -- and
deliberately not as a rendering."""


@pytest.mark.asyncio
async def test_the_snapshot_carries_every_station_and_every_buffer() -> None:
    line, clock = build_running_line()
    snapshot = line_snapshot(line, clock)
    assert [s["code"] for s in snapshot["stations"]] == ["S1", "S2", "S3", "S4"]
    assert [b["code"] for b in snapshot["buffers"]] == ["B1_2", "B2_3", "B3_4"]


@pytest.mark.asyncio
async def test_a_suspended_station_carries_its_reason_to_the_screen() -> None:
    """The HMI's whole diagnostic value is showing *why* a station is waiting."""
    line, clock = build_running_line()
    line.hold("S2", "test")
    for _ in range(40):
        await line.step()
    s3 = next(s for s in line_snapshot(line, clock)["stations"] if s["code"] == "S3")
    assert s3["category"] == "waiting-on-others"
    assert s3["reason"] == "starved:B2_3"


@pytest.mark.asyncio
async def test_a_held_station_is_a_cause_candidate_and_a_starved_one_is_not() -> None:
    """§3.3's distinction, carried to the screen as a category rather than left for
    the viewer to infer from a state name."""
    line, clock = build_running_line()
    line.hold("S2", "jam")
    for _ in range(40):
        await line.step()
    by_code = {s["code"]: s for s in line_snapshot(line, clock)["stations"]}
    assert by_code["S2"]["category"] == "held-by-own-fault"
    assert by_code["S3"]["category"] == "waiting-on-others"


def test_every_packml_state_maps_to_exactly_one_category() -> None:
    """A state with no category renders as nothing, which on a plant screen is worse
    than rendering wrong."""
    assert {category_for(state) for state in State} <= set(CATEGORIES)
    assert all(category_for(state) is not None for state in State)
```

Both this module and Task 12's `test_propagation.py` need a line that is actually running, so it goes in `plant/simulator/tests/conftest.py` once rather than being written twice:

```python
def build_running_line(
    settings: Settings | None = None,
) -> tuple[Line, SimulatedClock]:
    """A four-station line with recording node sets, built the way server.main builds
    the real one -- same station classes, same buffer capacities, same carrier count.

    Returns the clock too, because the HMI snapshot reports it and the propagation
    proof stamps its assertions with it.
    """
    settings = settings or Settings()
    clock = SimulatedClock(
        ClockConfig(
            history_depth=timedelta(hours=1),
            catchup_speed=settings.catchup_speed,
        )
    )
    nodes = {code: RecordingNodes(code) for code in STATION_CODES}

    async def produce(serial: str, at: datetime) -> PartOutcome:
        # No inspection service in a unit test. Deterministic rather than random: a
        # proof that failed one run in twenty would be worse than no proof.
        reject = serial.endswith(("3", "7"))
        return PartOutcome(
            disposition="reject" if reject else "good",
            defect_class="gap" if reject else None,
            confidence=0.93,
            image=b"PNG" if reject else None,
            model_version="test-1",
        )

    stations = [
        FeedingStation(nodes["S1"], settings, seed=settings.seed),
        JoiningStation(nodes["S2"], settings, seed=settings.seed),
        InspectionStation(nodes["S3"], settings, seed=settings.seed, produce=produce),
        OutfeedStation(nodes["S4"], settings, seed=settings.seed),
    ]
    buffers = [
        Buffer(code, settings.buffer_capacity, up, down)
        for code, up, down in (
            ("B1_2", "S1", "S2"),
            ("B2_3", "S2", "S3"),
            ("B3_4", "S3", "S4"),
        )
    ]
    line = Line(stations, buffers, CarrierPool(settings.carrier_count))
    line.seed(clock.history_start)
    return line, clock
```

- [ ] **Step 2: Write `hmi.py`**

A `FastAPI` app with one `GET /health`, one `GET /snapshot`, and one `WS /ws` that pushes `line_snapshot(...)` every `settings.hmi_interval_seconds` (default 0.5 — below one takt, so the screen is never a part behind). `category_for(state: State) -> str` implements the table above and is exhaustive by test.

The app is mounted on a uvicorn `Server` started inside `server.main`'s existing TaskGroup — not a second process, and not a second cadence.

- [ ] **Step 3: Scaffold `plant/hmi/`**

Mirror `diagnostics/ui/`'s toolchain exactly — same pinned versions of React 19, Vite 8, vitest, oxlint, prettier, typescript, and the same `Dockerfile`/`nginx.conf` shape. Copy the structure; do **not** import from `diagnostics/ui` — §10.7 forbids the two stacks depending on each other, and the two frontends are in different stacks.

`src/` holds `App.tsx`, `Line.tsx` (the four stations and three buffers), `StationTile.tsx`, `BufferBar.tsx`, `ClockPanel.tsx`, `useLineSnapshot.ts` (the WebSocket hook, with reconnect). Tests: `__tests__/line.test.tsx` asserting a suspended station renders its reason text and that the category class and the state text both appear.

- [ ] **Step 4: Add the container and the Make targets**

In `plant/compose.yml`, a `plant-hmi` service on `networks: [plant-net]` with the shared `x-runtime` block, published on `127.0.0.1:${PLANT_HMI_PORT:-5174}:80`. Read the published port from the environment with a default, as the M1 demo does — hardcoding it meant the demo could not run on the machine it was written on.

The `in-ui` Makefile macro is hardcoded to `$(UI)`. Parameterise it:

```make
define in-frontend
	@if [ -d "$(1)" ]; then cd "$(1)" && $(2); \
	else echo "skip [no $(1) in this checkout]: $(2)"; fi
endef
```

and call it for both `$(UI)` and `$(HMI)` from `lint-frontend`, `test-frontend` and `fmt`. The HMI has no generated types, so it gets the `pnpm generate && git diff --exit-code` step **not at all** rather than a no-op — a gate step that checks nothing is worse than no step.

- [ ] **Step 5: Verify the invariant test still passes**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_compose_invariants.py -v
```

Expected: PASS. `test_only_the_simulator_and_the_gateway_ever_join_field_net` is the one that would catch a mistake here, and it must stay green with no edit — if it needed editing to accommodate `plant-hmi`, the container is on the wrong network.

- [ ] **Step 6: Commit** — `feat(plant): an HMI that colours by cause and consequence, not by state`. The body must justify `fastapi` and `uvicorn` entering the `simulator` package: already resolved in the workspace by the inspection service, no new version in the graph, and the alternative — a second process polling the status snapshot — makes the snapshot interval the frame rate and still needs its own channel back in for M2c.

---

## Task 12: The propagation proof, and a spec claim that does not survive contact

M2a's authenticity proof: **stop S2, and S3 starves once B2_3 drains.** Measured, not asserted.

> **§3.1's thirty seconds holds only because of the ruling already applied.** A balanced line — all four stations on one nominal takt — leaves every buffer oscillating between 0 and 1, because each station consumes exactly as fast as the one above produces. Stopping S2 would then starve S3 after roughly *one* takt, and buffer capacity would bound nothing at all.
>
> §3.1 and Task 3's configuration now make **S3 the bottleneck at 6 s**, with S1 and S2 faster so that B1_2 and B2_3 fill. That is what gives §3.1's figure something to be true about.
>
> **Do not hardcode 30 s in the proof.** The test derives the expected delay from the buffer level observed at the moment S2 stops, so it survives the retuning this task may well conclude is needed.

**Files:**
- Create: `plant/simulator/tests/test_propagation.py`, `measurements/authenticity/README.md`
- Modify: `Makefile`, `README.md`

- [ ] **Step 1: Write the propagation proof**

```python
"""M2a's authenticity proof. §1's standard: a test that would fail if the link were a
facade. The facade here would be a line whose stations stop in sympathy rather than
because a buffer actually drained."""


@pytest.mark.asyncio
async def test_s3_starves_exactly_when_b2_3_drains_and_not_before() -> None:
    line, _ = build_running_line()
    for _ in range(200):  # reach steady state
        await line.step()

    level_at_stop = line.buffers[1].level  # B2_3
    line.hold("S2", "propagation proof")

    takts_until_starved = 0
    while line.machine_for("S3").state is not State.SUSPENDED:
        outcome = await line.step()
        assert outcome is not None
        if outcome.station_code == "S3":
            takts_until_starved += 1
        assert takts_until_starved <= level_at_stop + 2, "S3 never starved"

    # Derived, never hardcoded: §3.1's "roughly 30 s" assumes a buffer at capacity,
    # which a balanced line does not produce. What is always true is that S3 runs
    # exactly as many more parts as B2_3 was holding.
    assert takts_until_starved == level_at_stop + 1
    assert line.machine_for("S3").reason == "starved:B2_3"


@pytest.mark.asyncio
async def test_s4_starves_after_s3_and_not_at_the_same_time() -> None:
    """Propagation is a sequence, not an event. If both suspend on the same cycle the
    buffers are not decoupling anything and the whole model is decorative."""
    line, _ = build_running_line()
    for _ in range(200):
        await line.step()
    line.hold("S2", "propagation proof")

    suspended_at: dict[str, int] = {}
    for cycle in range(400):
        await line.step()
        for code in ("S3", "S4"):
            if (
                code not in suspended_at
                and line.machine_for(code).state is State.SUSPENDED
            ):
                suspended_at[code] = cycle
        if len(suspended_at) == 2:
            break

    assert set(suspended_at) == {"S3", "S4"}
    assert suspended_at["S3"] < suspended_at["S4"]


@pytest.mark.asyncio
async def test_s2_itself_is_held_rather_than_suspended() -> None:
    """§3.3's cause/consequence split, which is the thing M3 will reason over: the
    station that *is* the problem must not look like the ones merely waiting."""
    line, _ = build_running_line()
    for _ in range(200):
        await line.step()
    line.hold("S2", "propagation proof")
    for _ in range(200):
        await line.step()

    assert line.machine_for("S2").state is State.HELD
    assert line.machine_for("S2").clears_itself is False
    assert line.machine_for("S3").state is State.SUSPENDED
    assert line.machine_for("S3").clears_itself is True
```

- [ ] **Step 2: Run it, and report which reading of §3.1 the numbers support**

```bash
cd plant && uv run --package simulator pytest simulator/tests/test_propagation.py -v
```

**Record the observed steady-state level of each buffer, and the free-carrier count, in the commit message.** Three things are being confirmed rather than assumed:

1. **B1_2 and B2_3 reach capacity, and B3_4 does not.** If they do not, the takt spread is too small and S3 is not really the bottleneck.
2. **S1 does not suspend on `carrier-return` during normal running.** Two full buffers park 10 of 12 carriers; if the pool is exhausted in steady state, `carrier_count` rises.
3. **S4 does not starve on every cycle.** S4 matching S3's nominal is what avoids that. Frequent but not constant starvation is correct, and is exactly the "within normal spread" judgement M3 will have to make.

- [ ] **Step 3: Write the file M1 marked done and never committed**

Create `measurements/authenticity/README.md`. M1's plan Task 15 Step 3 is checked but this file does not exist and the list lives nowhere. It names every §1 proof not yet makeable and the milestone each waits on:

| §1 proof | Waits on | Why not yet |
|---|---|---|
| Analysis matches the simulator's ground truth, no model involved | M2c + M3 | M2c produces ground truth; M3 does the matching |
| The agent says "I have no data for that window" rather than inventing | M4 | M1 tests the empty-window case only, not the behaviour |
| An external MCP client reaches the same tools | M4 | no MCP server yet |
| Unauthenticated requests return 401, MCP included | M5 | no identity layer yet |
| A containment query returns exactly the affected serials, scored | M7 | needs genealogy (M2b) and the harness (M7) |

Add M2a's own proof to the *makeable* list: propagation is real and measured (`test_propagation.py`), and `pg_rows == plant_rows` at 25 streams (Task 10).

- [ ] **Step 4: Extend the demo**

Add `m2a-demo` to the `Makefile`, following `m1-demo`'s shape and reading every published port from the environment. Its distinctive step is the one M1 could not show: bring the line up, watch all four stations reach `Execute` on the HMI, then **hold S2 from the HMI and watch the amber spread downstream while S2 alone goes red** — the cause/consequence distinction, visible rather than argued.

- [ ] **Step 5: Update the README's "Known limits"**

The line *"M1 is one station, two signals and one event. Four stations, buffers, carriers, PackML and the eight scenarios are M2"* is now partly false. Replace it with what is true after M2a — four stations, PackML, buffers and carriers are real; serials, genealogy and per-part process values are M2b; the eight scenarios and the noise floor are M2c — and say that the plant runs a fixed nominal takt with no injected faults yet, so every stop visible today is one you caused by hand.

- [ ] **Step 6: Run the whole gate and the authenticity proofs**

```bash
make check
make verify
```

- [ ] **Step 7: Commit** — `test(plant): propagation is measured, and §3.1's thirty seconds is not what a balanced line does`. The body carries the observed buffer levels and states plainly which of (a) or (b) the evidence supports, leaving the ruling to the spec owner.

---

## Self-review

Run against the spec with fresh eyes, per the writing-plans skill.

**Spec coverage.** M2a's slice of the M2 design doc: D1 → Task 4; D2 → Task 5; D3 → Task 9; D4 → Task 10; D5 → Task 11; D9 → Task 1 (measured) ; D10 → Task 3's config; D12 → Task 6; D13 → recorded in Task 5, deleted in M2c. Migration `002` → Task 8. Topology discovery of buffer references → Task 9. The thin HMI → Task 11. The two M2a proofs → Tasks 10 and 12. `measurements/authenticity/README.md` → Task 12. **D6, D7, D8 and D11 are M2b/M2c and correctly absent.**

**Gaps found and closed while reviewing:**
1. Buffers held an integer level, which M2b's assembly serials could not ride on — fixed in Task 3 before Task 4 depended on it.
2. S4 needs S3's verdict for `GoodCount`/`RejectCount` — `PartState` added to Task 4 rather than leaving Task 5 to invent a channel.
3. `OutfeedStation.__init__` was drafted with `*args: object`, which `disallow_any_explicit` rejects and which CLAUDE.md's suppression rule rules out — the explicit signature is given instead.

**Open questions for the spec owner, raised rather than absorbed:**
- **§4.1's "reference"** — implemented as `String` variables carrying the station browse name, not a custom OPC UA reference type (Task 6). Topology is discovered either way.
- ~~**§3.1's "roughly 30 s"** — does not hold on a balanced line.~~ **Ruled and applied:** S3 paces the line at 6 s and the stations above it run faster, so their buffers fill. §3.1 rewritten, Task 3 carries the per-station takts, Task 12 confirms the levels and the carrier headroom.
- **§15's stream count** — corrected to 25 in the spec revision; every guard in Tasks 9 and 10 is sized against that number.

**Type consistency.** `StationNodes` (Protocol, Task 5) is implemented by `StationNodeSet` (Task 6). `StationCycle` (Protocol, Task 4) is implemented by `Station` (Task 5) and its four subclasses. `PartState` is defined in Task 4 and consumed in Tasks 4, 5 and 11. `Ledger.record(station_code, signal)` (Task 7) is keyed the same way `state_changes` and `signals` are keyed in Task 8. `PartOutcome` and `serial_for` move from `station_s3.py` to `stations/base.py` unchanged.

---

## Execution order

Tasks 1–7 are the plant and must run in order — each builds on the last's types. Task 8 (migration + writer) and Task 11 (HMI) are independent of each other and of Tasks 9–10 once Task 7 lands. Task 12 needs everything.

**Task 5 and Task 7 commit together.** Deleting `station_s3.py` breaks `test_generation.py` and `test_inspection_client.py` until Task 7 rewrites them, and a red `make check` must not be committed.
