"""S3 Inspection: the takt loop, catch-up generation and live production."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from asyncua import ua
from asyncua.server.history_sql import HistorySQLite

from simulator.address_space import AddressSpace
from simulator.clock import Phase, SimulatedClock
from simulator.config import Settings
from simulator.historian import Ledger


@dataclass(frozen=True)
class PartOutcome:
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
    return f"A-{index:08d}"


_MAX_TAKT_RESAMPLES = 100
"""Bounds _next_takt's resample loop. A well-formed positive sigma finds a value
distinct from the previous one on the first or second draw essentially always;
this exists so a degenerate configuration -- takt_jitter_sigma=0, the obvious way
someone turns jitter off, or anything else that makes every draw collide with the
previous value -- fails loudly and immediately instead of spinning forever inside
a non-yielding while loop in an async function, wedging the whole event loop with
no error, timeout, or log. That is the same hang-on-bad-input shape the previous
review round flagged on _await_historian_settle, recreated here in its
replacement; this closes it the same way, with a bound."""


def _next_takt(
    rng: random.Random, nominal: float, sigma: float, previous: float | None
) -> float:
    """Additive Gaussian jitter on the takt, resampled until distinct from the
    previous value.

    Needed for TaktTime to be historised at all, not for realism: asyncua's own
    monitored-item filter (DataChangeTrigger.StatusValue, the default) drops a
    notification whenever the written value is unchanged, regardless of
    SourceTimestamp. A bare constant takt -- M1's default, since §3.5's actual noise
    model is M2's, not this -- means only the very first write of a run is ever
    historised; every later part with the same value is silently dropped before it
    ever reaches the storage layer. The resample-on-collision loop below is what
    makes the historian's row count equal the ledger's by construction, rather than
    by the odds of two float64 Gaussian draws colliding (vanishingly small, but
    R1 asserts exact equality, not "usually").

    Raises ValueError if _MAX_TAKT_RESAMPLES consecutive draws all equal `previous`
    -- see that constant's comment for why this is a bound, not a retry-forever.
    """
    for _ in range(_MAX_TAKT_RESAMPLES):
        value = nominal + rng.gauss(0.0, sigma)
        if value != previous:
            return value
    raise ValueError(
        f"takt_jitter_sigma={sigma!r} produced {_MAX_TAKT_RESAMPLES} consecutive "
        f"draws equal to the previous takt ({previous!r}); a sigma of 0 (or too "
        "small to move a float64 draw away from nominal) makes every draw equal "
        "nominal, which can never satisfy the resample guard"
    )


async def _emit_part(
    space: AddressSpace,
    index: int,
    sim_ts: datetime,
    takt: float,
    outcome: PartOutcome,
    ledger: Ledger,
) -> None:
    serial = serial_for(index)

    # Variables. write_value with an explicit SourceTimestamp only reaches the
    # historian if asyncua's own monitored-item filter judges the write an actual
    # change (identical values are silently coalesced -- see _next_takt for why
    # TaktTime is jittered) and only once the subscription's own publish loop, on
    # its ~10 ms schedule, delivers the queued notification (that queue caps at
    # 10,000 and drops the oldest past that -- see generate_history's pacing).
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
    ev.ModelVersion = outcome.model_version
    ev.Image = outcome.image or b""
    await space.event_gen.trigger(time_attr=sim_ts, message=f"inspection {serial}")
    ledger.events += 1
    if outcome.image:
        ledger.images += 1
        ledger.image_bytes += len(outcome.image)


_PUBLISH_TICK = 0.01
"""Seconds. Matches HistoryManager._create_subscription's RequestedPublishingInterval
(10, in the milliseconds the OPC UA CreateSubscriptionParameters type expects) --
asyncua's own hardcoded period between publish-loop ticks, not a value this project
chose. Used only to pace _reconcile_with_historian's polling, below."""

_RECONCILE_TIMEOUT_SECONDS = 5.0
"""Generous relative to the ~10 ms tick above: bounds _reconcile_with_historian so a
genuine mismatch (rows the publish loop's queue cap silently dropped, say) raises
promptly instead of polling forever, rather than a guess at how long settling
normally takes."""


def _table_name(storage: HistorySQLite, node_id: ua.NodeId) -> str:
    """HistorySQLite has no public "how many rows for this node" query -- reuses its
    own private table-naming method rather than re-deriving the "{ns}_{id!r}" scheme
    by hand, so the two can never drift apart."""
    get_table_name: Callable[[ua.NodeId], str] = storage._get_table_name
    return get_table_name(node_id)


async def _count_rows(storage: HistorySQLite, node_id: ua.NodeId) -> int:
    """A direct SQL COUNT(*) against the sqlite file HistorySQLite itself owns, not
    read_raw_history(): counting rows needs no datetime comparison at all, so --
    unlike paging *values* across a start/end boundary -- it never touches
    read_node_history's timezone-naive continuation point (generate_history's own
    docstring has that trap)."""
    table = _table_name(storage, node_id)
    async with storage._db.execute(f'SELECT COUNT(*) FROM "{table}"') as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError(f"COUNT(*) on {table!r} returned no row")
    return int(row[0])


async def _historian_row_counts(
    space: AddressSpace, storage: HistorySQLite
) -> dict[str, int]:
    return {
        "takt": await _count_rows(storage, space.takt.nodeid),
        "part_count": await _count_rows(storage, space.part_count.nodeid),
        "events": await _count_rows(storage, space.s3.nodeid),
    }


async def _reconcile_with_historian(
    space: AddressSpace, storage: HistorySQLite, ledger: Ledger
) -> None:
    """Polls the historian's own row counts -- not asyncio task completion -- until
    they match the ledger or a timeout elapses, then raises if they still disagree.

    A ledger that only counts what _emit_part *called* write_value/trigger for is
    not the same claim as "the historian holds that many rows", and R1 needs the
    second one: the queue-cap defect this project measured at production depth (a
    monitored item's notification queue caps at 10,000 and silently drops the
    oldest entry past that) left every fire-and-forget storage task completing
    successfully -- the notifications for the discarded rows were never enqueued in
    the first place, so polling task.done() alone would have reported a clean
    settle while 9,800 rows were already gone. Counting the actual rows is what
    catches that.
    """
    expected = {
        "takt": ledger.takt,
        "part_count": ledger.part_count,
        "events": ledger.events,
    }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RECONCILE_TIMEOUT_SECONDS
    while True:
        counts = await _historian_row_counts(space, storage)
        if counts == expected:
            return
        if loop.time() >= deadline:
            raise RuntimeError(
                f"historian disagrees with the ledger after settling: "
                f"historian={counts} ledger={expected}"
            )
        await asyncio.sleep(_PUBLISH_TICK)


async def generate_history(
    space: AddressSpace,
    clock: SimulatedClock,
    settings: Settings,
    produce: ProduceFn,
    ledger: Ledger,
    storage: HistorySQLite,
) -> None:
    """Catch-up: build the configured depth of history in process (§3.2).

    Reads the historian back through `storage` before returning and raises if it
    disagrees with the ledger (see _reconcile_with_historian) -- so a caller that
    gets a normal return from this function has R1's guarantee already checked, not
    just attempted.

    Paced in batches of `settings.catchup_batch_size` parts, sleeping
    `settings.catchup_batch_pause_seconds` between them: asyncua's own per-stream
    notification queue caps at 10,000 and silently drops the oldest entry past that
    (confirmed at this task's production depth -- 19,800 parts overflowed it and
    lost the oldest 9,800 rows per stream with no pacing at all), so nothing here
    may run the whole depth in one uninterrupted burst.

    TaktTime's value is jittered (_next_takt) and SourceTimestamp advances by that
    same jittered interval, cumulatively, rather than a fixed i * takt grid, so the
    written value and the timestamp gap it produces always agree. The row count
    stays exact regardless, since the loop is range(total).

    The convention is deliberately forward-looking: the value written for part i is
    the interval that will elapse before part i+1, not the interval since part
    i-1. A query asking "what was the takt at instant T" should look at the next
    row after T, not the row at or before it -- the reverse (backward-looking, the
    cycle that just completed) is equally self-consistent, it just needs a special
    case for the very first part, which has no prior cycle to report. Either is a
    valid choice; this is the one this project made.

    Do not page the storage in-process to verify: HistorySQLite returns a
    timezone-naive continuation point and comparing it against an aware datetime
    raises TypeError. Read back through the server instead (which is exactly what
    _reconcile_with_historian's read_raw_history-free row count, and
    test_source_timestamps_are_simulated_not_wall_clock's read_raw_history, both do).
    """
    rng = random.Random(settings.seed)
    nominal = settings.takt_seconds
    total = int(clock.history_depth.total_seconds() // nominal)
    running_ts = clock.history_start
    previous_takt: float | None = None
    for i in range(total):
        interval = _next_takt(rng, nominal, settings.takt_jitter_sigma, previous_takt)
        sim_ts = running_ts
        await _emit_part(
            space,
            i,
            sim_ts,
            interval,
            await produce(serial_for(i), sim_ts),
            ledger,
        )
        previous_takt = interval
        running_ts = running_ts + timedelta(seconds=interval)
        if (i + 1) % settings.catchup_batch_size == 0:
            await asyncio.sleep(settings.catchup_batch_pause_seconds)
    await _reconcile_with_historian(space, storage, ledger)


async def run_live(
    space: AddressSpace,
    clock: SimulatedClock,
    settings: Settings,
    produce: ProduceFn,
    ledger: Ledger,
    start_index: int,
) -> None:
    """Live: one part per takt at exactly 1.0 (§3.2).

    Uses its own RNG stream (settings.seed XORed against a distinguishing constant,
    not a continuation of generate_history's stream) so that changing the configured
    history depth -- which changes how many draws catch-up consumes -- can never
    shift what run_live produces for the same seed.

    Seeds the resample guard from TaktTime's current value in the address space --
    whatever generate_history last wrote, or the priming row if it never ran --
    rather than None: without this, the first live part could collide with the
    last catch-up part's value (both drawn independently), the one place the
    "distinct from the previous value, by construction" guarantee would not
    actually hold across the catch-up/live seam.

    Sleeps the interval it just wrote, not a fixed settings.takt_seconds: TaktTime
    is a claim about how long the cycle takes, and waiting a different amount than
    it reports would be exactly the kind of quiet, faked measurement this project
    does not allow. settings.takt_seconds is used only to poll while still waiting
    out Phase.CATCHUP, when no cycle is being timed yet.
    """
    rng = random.Random(settings.seed ^ 1)
    previous_takt: float | None = float(await space.takt.read_value())
    index = start_index
    while True:
        if clock.phase is Phase.LIVE:
            sim_ts = clock.now()
            interval = _next_takt(
                rng, settings.takt_seconds, settings.takt_jitter_sigma, previous_takt
            )
            await _emit_part(
                space,
                index,
                sim_ts,
                interval,
                await produce(serial_for(index), sim_ts),
                ledger,
            )
            previous_takt = interval
            index += 1
            await asyncio.sleep(interval)
        else:
            await asyncio.sleep(settings.takt_seconds)
