"""S3 Inspection: the takt loop, catch-up generation and live production."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from asyncua import ua

from simulator.address_space import AddressSpace
from simulator.clock import Phase, SimulatedClock
from simulator.config import Settings
from simulator.historian import Ledger

MODEL_VERSION = "simulated-1"


@dataclass(frozen=True)
class PartOutcome:
    disposition: str  # "good" | "reject"
    defect_class: str | None
    confidence: float
    image: bytes | None  # §3.4: only rejects carry their image


ProduceFn = Callable[[str, datetime], Awaitable[PartOutcome]]


def serial_for(index: int) -> str:
    return f"A-{index:08d}"


_PUBLISH_TICK = 0.01
"""Seconds. Matches HistoryManager._create_subscription's RequestedPublishingInterval
(10, in the milliseconds the OPC UA CreateSubscriptionParameters type expects) --
asyncua's own hardcoded period between publish-loop ticks, not a value this project
chose. Used only to pace _await_historian_settle's polling, below."""

_MIN_SETTLE_TICKS = 5
"""However quiet things look immediately after the loop, the publish tick that
delivers our own last write hasn't necessarily fired yet -- so this is a floor on
how long _await_historian_settle waits before it ever trusts an empty pending set,
not a guess at how long settling normally takes."""

_QUIET_TICKS_NEEDED = 5
"""Consecutive clean samples required once the floor above has passed, before
_await_historian_settle decides the backlog has actually drained rather than just
being between two of asyncua's publish-loop ticks."""


async def _await_historian_settle(pre_existing: frozenset[asyncio.Task[None]]) -> None:
    """write_value()/trigger() only guarantee the in-memory address space is
    updated. The historian save runs separately: asyncua queues the notification
    and only creates the actual storage-write task (SubHandler in
    asyncua.server.history) from its own internal publish loop, which wakes on its
    own schedule (asyncua.server.internal_subscription._subscription_loop) rather
    than inline with our write -- so nothing here can be caught by yielding once;
    it has to wait for that loop to actually tick.

    Without this, generate_history can report catch-up done while its own writes
    are still in flight -- the exact silent loss R1 exists to catch, one layer
    earlier than R1 looks. Polls task.done() only, never awaits/gathers the tasks
    themselves: asyncua 2.0.1's event-history path has its own, separate, confirmed
    bug (historian.py's docstring has the detail) that raises from inside every
    fire-and-forget event-save task, and gathering one would turn today's silent,
    asyncio-logged warning into a crash in our own code for a bug on the far side
    of a library we cannot fix from here. .done() reports completion regardless of
    whether a task succeeded or raised, so this settles the whole backlog -- data-
    change and event saves alike -- without ever touching what either returned.
    """
    current = asyncio.current_task()
    for _ in range(_MIN_SETTLE_TICKS):
        await asyncio.sleep(_PUBLISH_TICK)
    quiet_ticks_needed = _QUIET_TICKS_NEEDED
    while quiet_ticks_needed > 0:
        await asyncio.sleep(_PUBLISH_TICK)
        pending = any(
            t is not current and t not in pre_existing and not t.done()
            for t in asyncio.all_tasks()
        )
        quiet_ticks_needed = _QUIET_TICKS_NEEDED if pending else quiet_ticks_needed - 1


async def _emit_part(
    space: AddressSpace,
    index: int,
    sim_ts: datetime,
    takt: float,
    outcome: PartOutcome,
    ledger: Ledger,
) -> None:
    serial = serial_for(index)

    # Variables. write_value with an explicit SourceTimestamp reaches the historian
    # through the internal datachange subscription, which fires per change rather
    # than per publishing interval -- so catch-up rates do not coalesce values.
    #
    # Both SourceTimestamp= below carry a suppression for the same reason. DataValue
    # types the field as ua.DateTime, a datetime subclass asyncua's own runtime
    # never actually constructs (it assigns plain datetimes throughout), and
    # building a real ua.DateTime here is not a stricter-but-equivalent fix --
    # confirmed by running it: sqlite3's parameter binder matches by exact type, not
    # by subclass, since Python 3.12 deprecated the old implicit adapter that
    # covered subclasses too, so a bound ua.DateTime raises "Error binding
    # parameter: type 'DateTime' is not supported" inside
    # HistorySQLite.save_node_value's own try/except and is silently logged, not
    # raised -- every row goes missing with no visible error. A plain datetime is
    # what the storage layer actually needs; the annotation is just narrower than
    # what its own implementation requires.
    await space.takt.write_value(
        ua.DataValue(
            ua.Variant(takt, ua.VariantType.Double),
            SourceTimestamp=sim_ts,  # type: ignore[arg-type]
        )
    )
    ledger.takt += 1
    await space.part_count.write_value(
        ua.DataValue(
            ua.Variant(index + 1, ua.VariantType.UInt32),
            SourceTimestamp=sim_ts,  # type: ignore[arg-type]
        )
    )
    ledger.part_count += 1

    # The event. Its Time field is the event analogue of SourceTimestamp.
    ev = space.event_gen.event
    ev.AssemblySerial = serial
    ev.Disposition = outcome.disposition
    ev.DefectClass = outcome.defect_class or ""
    ev.Confidence = outcome.confidence
    ev.ModelVersion = MODEL_VERSION
    ev.Image = outcome.image or b""
    await space.event_gen.trigger(time_attr=sim_ts, message=f"inspection {serial}")
    ledger.events += 1
    if outcome.image:
        ledger.images += 1
        ledger.image_bytes += len(outcome.image)


async def generate_history(
    space: AddressSpace,
    clock: SimulatedClock,
    settings: Settings,
    produce: ProduceFn,
    ledger: Ledger,
) -> None:
    """Catch-up: build the configured depth of history in process (§3.2).

    Timestamps are computed directly from the takt rather than sampled from the
    clock, so the history is exactly regular and the expected row count is known
    in advance -- which is what makes R1's reconciliation meaningful.

    Do not page the storage in-process to verify: HistorySQLite returns a
    timezone-naive continuation point and comparing it against an aware datetime
    raises TypeError. Read back through the server instead.
    """
    current = asyncio.current_task()
    pre_existing = frozenset(t for t in asyncio.all_tasks() if t is not current)
    takt = settings.takt_seconds
    total = int(clock.history_depth.total_seconds() // takt)
    for i in range(total):
        sim_ts = clock.history_start + timedelta(seconds=i * takt)
        await _emit_part(
            space, i, sim_ts, takt, await produce(serial_for(i), sim_ts), ledger
        )
    await _await_historian_settle(pre_existing)


async def run_live(
    space: AddressSpace,
    clock: SimulatedClock,
    settings: Settings,
    produce: ProduceFn,
    ledger: Ledger,
    start_index: int,
) -> None:
    """Live: one part per takt at exactly 1.0 (§3.2)."""
    index = start_index
    while True:
        if clock.phase is Phase.LIVE:
            sim_ts = clock.now()
            await _emit_part(
                space,
                index,
                sim_ts,
                settings.takt_seconds,
                await produce(serial_for(index), sim_ts),
                ledger,
            )
            index += 1
        await asyncio.sleep(settings.takt_seconds)
