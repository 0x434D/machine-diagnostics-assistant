"""Historian wiring, the ledger it is reconciled against, and the traps that bite here."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from asyncua import Server, ua
from asyncua.server.history_sql import HistorySQLite

from simulator.address_space import (
    BUFFER_LEVEL_SIGNAL,
    INSPECTION_STATION,
    AddressSpace,
    StationNodeSet,
    historised_streams,
    initial_value,
    write_at,
)

EVENT_SIGNAL = "InspectionResult"
"""How S3's event stream is keyed, alongside §4.1's 25 data streams. Not a variable
name in the tree -- event history hangs off the emitting station node itself -- so
this names the event type rather than a signal."""


@dataclass
class Ledger:
    """What the generator believes the historian holds, per stream.

    Compared against the historian's own row counts before catch-up returns (see
    `LedgerWriter.reconcile`). The two are different claims, and R1 needs the second
    one.

    **A write is not a row, so this counts rows.** asyncua's monitored-item filter --
    `DataChangeTrigger.StatusValue`, the default, and the only one
    `historize_node_data_change` installs -- drops a notification whose DataValue
    carries the same StatusCode and the same Variant as the one before it, regardless
    of SourceTimestamp. A stream is therefore historised only where it actually
    changes, and a ledger counting `write_value` calls would over-count every stream
    but `TaktTime`: S4 writes `GoodCount` and `RejectCount` on every part and only one
    of them moves, `State` and `StateReason` repeat for as long as a station stays put,
    and rounded floats collide outright (`OutfeedFill`'s `round(..., 3)` produces
    consecutive equal values on a few hundredths of a percent of parts at the
    configured sigma -- small, and R1 asserts exact equality, not "usually").
    `record` applies asyncua's own rule instead, which is what makes "the historian
    equals the ledger" a claim that can be true rather than one that can only fail.

    The one thing this deliberately cannot model is the defect it exists to catch: a
    notification the publish loop's 10,000-entry queue cap silently discarded. That
    gap is exactly what reconciliation looks for.
    """

    rows: dict[tuple[str, str], int] = field(default_factory=dict)
    events: int = 0
    images: int = 0
    # Not a row count: R1 also reconciles storage volume against Postgres, and a
    # reject-image byte total is the one number here a row count cannot stand in for.
    image_bytes: int = 0
    # The previous value per stream, which is the whole of the rule above. Kept here
    # rather than in the writer because it is the ledger's claim that depends on it.
    _last_value: dict[tuple[str, str], float | str] = field(
        default_factory=dict, repr=False
    )

    def record(self, owner: str, signal: str, value: float | str) -> None:
        """Count the row this write becomes -- or nothing, if it becomes no row.

        `owner` is a station browse name or a buffer id; with `signal` it names one
        of §4.1's 25 streams.
        """
        key = (owner, signal)
        # `!=` on the raw value, because that is the comparison asyncua makes on the
        # Variant wrapping it -- down to 0.0 == -0.0 and 1 == 1.0. Anything stricter
        # here (`is`, a type check) would count rows the historian does not hold.
        if key in self._last_value and self._last_value[key] == value:
            return
        self._last_value[key] = value
        self.rows[key] = self.rows.get(key, 0) + 1

    def rows_by_name(self) -> dict[str, int]:
        """`{"S1_Feeding.TaktTime": n, ...}`, for anything that has to serialise this.

        A `(station, signal)` tuple is not a JSON object key, and `simulator.status`
        writes this file for a reader with nothing but `cat`.
        """
        return {
            f"{owner}.{signal}": count
            for (owner, signal), count in sorted(self.rows.items())
        }


@dataclass(frozen=True)
class _LedgerStation:
    """One station's `stations.StationNodes`, with every write counted.

    Satisfies that Protocol structurally rather than by inheriting it: the Protocol
    lives in `stations.base`, which imports `line`, which imports this module -- and
    a Protocol never needs the import anyway.
    """

    nodes: StationNodeSet
    ledger: Ledger

    @property
    def code(self) -> str:
        return self.nodes.code

    async def write(self, signal: str, at: datetime, value: float | str) -> None:
        await self.nodes.write(signal, at, value)
        self.ledger.record(self.nodes.code, signal, value)

    async def trigger_event(self, at: datetime, fields: dict[str, object]) -> None:
        # Triggered first, so a refused event (StationNodeSet.trigger_event raises on a
        # field set that is not EVENT_FIELDS) is not counted as one that happened.
        await self.nodes.trigger_event(at, fields)
        self.ledger.events += 1
        # Events are not data changes: there is no previous value to compare, so every
        # trigger is a row. The image is counted from what actually went on the wire
        # rather than from the PartOutcome behind it, because §3.4's claim is about
        # the event.
        image = fields["Image"]
        if isinstance(image, bytes) and image:
            self.ledger.images += 1
            self.ledger.image_bytes += len(image)


class LedgerWriter:
    """The line's one path to §4.1's historised nodes, counting what it writes.

    Every station is built against `station(code)` and the driver publishes buffer
    levels through `write_level`, so there is exactly one place a historised node is
    written outside the historian's own priming, and exactly one place that counts it.
    A station handed a raw `StationNodeSet` would keep working and would be invisible
    to the ledger -- a mismatch reconciliation would report as lost rows, pointing at
    the historian for a defect on this side.
    """

    def __init__(self, space: AddressSpace, ledger: Ledger) -> None:
        self._space = space
        self._ledger = ledger

    def station(self, code: str) -> _LedgerStation:
        """Raises KeyError for a station §4.1's tree does not carry."""
        return _LedgerStation(self._space.stations[code], self._ledger)

    async def write_level(self, buffer_id: str, at: datetime, level: int) -> None:
        """Publish one buffer's fill (§4.1). Raises KeyError for an unknown buffer."""
        await self._space.buffers[buffer_id].write_level(at, level)
        self._ledger.record(buffer_id, BUFFER_LEVEL_SIGNAL, level)

    async def historian_row_counts(
        self, storage: HistorySQLite
    ) -> dict[tuple[str, str], int]:
        """What the database actually holds, per stream, keyed as the ledger keys it."""
        counts = {
            (stream.owner, stream.signal): await _count_rows(
                storage, stream.node.nodeid
            )
            for stream in historised_streams(self._space)
        }
        counts[(INSPECTION_STATION, EVENT_SIGNAL)] = await _count_rows(
            storage, self._space.inspection.node.nodeid
        )
        return counts

    async def reconcile(self, storage: HistorySQLite) -> None:
        """Polls the historian's own row counts -- not asyncio task completion -- until
        they match the ledger or stop moving, then raises if they still disagree.

        A ledger that only counts what the line *attempted* is not the same claim as
        "the historian holds that many rows", and R1 needs the second one: the
        queue-cap defect this project measured at production depth (a monitored item's
        notification queue caps at 10,000 and silently drops the oldest entry past
        that) left every fire-and-forget storage task completing successfully -- the
        notifications for the discarded rows were never enqueued in the first place,
        so polling task.done() alone would have reported a clean settle while 9,800
        rows were already gone. Counting the actual rows is what catches that.

        Settled means the counts have stopped moving, not that a wall-clock budget has
        elapsed. M1's five-second budget was sized against three streams and 40,000
        rows; at 25 streams and ~380,000 it is shorter than a single poll, because
        every COUNT(*) queues behind the storage's own pending inserts on one sqlite
        connection -- so the first poll returned after the deadline had already passed
        and the run was judged on a count taken while the tail was still landing. It
        reported eight rows missing that were in the database twenty milliseconds
        later. Waiting for quiet instead makes the check independent of how much
        history was generated, which is the property a volume-shaped budget cannot
        have.

        The message names which streams disagree and by how much. At three streams
        "the historian disagrees with the ledger" was a usable message; at 25 it is an
        hour of bisecting to find out which one lost rows.
        """
        expected = dict(self._ledger.rows)
        expected[(INSPECTION_STATION, EVENT_SIGNAL)] = self._ledger.events
        loop = asyncio.get_running_loop()
        ceiling = loop.time() + _RECONCILE_CEILING_SECONDS
        previous: dict[tuple[str, str], int] = {}
        # A float, and `loop.time()` may legitimately be 0.0 -- initialised rather than
        # left None and defaulted with `or`, which would read that as "never".
        quiet_since = loop.time()
        while True:
            counts = await self.historian_row_counts(storage)
            if counts == expected:
                return
            now = loop.time()
            if counts != previous:
                previous = counts
                quiet_since = now
            if now - quiet_since >= _RECONCILE_QUIET_SECONDS or now >= ceiling:
                differing = {
                    key: (counts.get(key, 0), expected.get(key, 0))
                    for key in set(expected) | set(counts)
                    if counts.get(key, 0) != expected.get(key, 0)
                }
                raise RuntimeError(
                    f"historian disagrees with the ledger on {len(differing)} of "
                    f"{len(expected)} streams after settling: "
                    + ", ".join(
                        f"{owner}.{signal} historian={got} ledger={want}"
                        for (owner, signal), (got, want) in sorted(differing.items())
                    )
                )
            await asyncio.sleep(_PUBLISH_TICK)


_PUBLISH_TICK = 0.01
"""Seconds. Matches HistoryManager._create_subscription's RequestedPublishingInterval
(10, in the milliseconds the OPC UA CreateSubscriptionParameters type expects) --
asyncua's own hardcoded period between publish-loop ticks, not a value this project
chose. Used only to pace LedgerWriter.reconcile's polling."""

_RECONCILE_QUIET_SECONDS = 2.0
"""How long the historian's row counts must stop moving before a disagreement is
called one rather than a settle still in progress. Two hundred of the publish-loop
ticks above, so a stream still delivering is not mistaken for a stream that lost
rows -- and it is a length of *silence*, not a budget for the whole settle, so it does
not need re-measuring when the history depth or the stream count changes."""

_RECONCILE_CEILING_SECONDS = 60.0
"""A bound on the whole poll, not an estimate of how long settling takes -- that is
what the quiet period decides. This exists so that counts which somehow never stop
moving fail loudly instead of spinning in a loop nothing times out, the same reason
`stations.base._MAX_TAKT_RESAMPLES` is a bound. Generous against the ~43 s a 33 h
catch-up takes to write its ~380,000 rows in the first place."""


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
    read_node_history's timezone-naive continuation point."""
    table = _table_name(storage, node_id)
    async with storage._db.execute(f'SELECT COUNT(*) FROM "{table}"') as cursor:
        row = await cursor.fetchone()
    if row is None:
        raise RuntimeError(f"COUNT(*) on {table!r} returned no row")
    return int(row[0])


_EVENT_HISTORY_BUG_KEY = (ua.NodeId(ua.Int32(ua.ObjectIds.Server)),)
"""The exact KeyError asyncua.common.events.Event.from_event_fields' reconstruction
(see below) always raises from inside HistorySQLite.save_event's fire-and-forget
task -- used to filter only that one confirmed, understood exception out of the
default per-task logging, below."""


def _quiet_known_event_history_bug(
    loop: asyncio.AbstractEventLoop,
    context: dict[str, object],
    fallback: Callable[[asyncio.AbstractEventLoop, dict[str, object]], object] | None,
) -> None:
    """Filters one specific, confirmed asyncua bug out of asyncio's default
    per-task exception logging; everything else still gets the full report.

    Every event S3 triggers hits this exact KeyError inside HistorySQLite.save_event's
    own fire-and-forget task (see the long comment on historize_node_event below for
    the mechanism) -- once per event, drowning out anything else `make check` needs to
    show, and training readers to skim past tracebacks in a project whose whole thesis
    is not doing that. Matched on the exact KeyError value this one bug produces, not
    on exception type alone, so a genuinely new failure in the same task still
    surfaces normally.
    """
    exception = context.get("exception")
    if isinstance(exception, KeyError) and exception.args == _EVENT_HISTORY_BUG_KEY:
        return
    if fallback is not None:
        fallback(loop, context)
    else:
        loop.default_exception_handler(context)


async def attach_historian(
    server: Server,
    space: AddressSpace,
    db_path: Path,
    page_size: int,
    priming_source_timestamp: datetime,
    ledger: Ledger,
) -> HistorySQLite:
    """Historise §4.1's 25 streams and S3's events, priming every one of them first.

    `priming_source_timestamp` must be a simulated instant outside the window
    catch-up will fill -- one takt before `clock.history_start` is what `server.main`
    passes.
    """
    storage = HistorySQLite(str(db_path), max_history_data_response_size=page_size)

    # HistoryManager.init() already ran inside Server.init() against the default
    # in-memory HistoryDict, so replacing the storage means initialising it here.
    await storage.init()
    server.iserver.history_manager.set_storage(storage)

    # ORDER MATTERS: prime every stream, then historise. OPC UA's own
    # subscribe_data_change delivers an initial-value notification at subscribe time,
    # using whatever value and SourceTimestamp the node currently holds, and that
    # notification unavoidably becomes each variable's first historised row -- 25 of
    # them, before any station has written anything. Left as the wall-clock timestamp
    # add_variable gave it, every one of those rows violates the project invariant
    # that SourceTimestamp is simulated, and nothing downstream fails loudly when it
    # happens: the rows are there, the counts reconcile, and the timeline simply has
    # 25 entries from a clock that is not the plant's. Rewriting each declared value
    # with a deliberate simulated stamp first fixes the timestamp and lets the ledger
    # count the row truthfully (below) rather than excluding a row that is, after
    # this, a real and correctly timestamped one.
    #
    # The value written is the one the tree declares (address_space.initial_value), so
    # the write changes nothing but the timestamp -- which is what makes these rows
    # honest rather than a generated zero that the plant was never in.
    streams = list(historised_streams(space))
    for stream in streams:
        value = initial_value(stream.signal, stream.variant_type)
        await write_at(
            stream.node, stream.variant_type, priming_source_timestamp, value
        )
        ledger.record(stream.owner, stream.signal, value)

    # period=None, count=0 is deliberate, not laziness. save_node_value() deletes
    # rows where SourceTimestamp < now() - period using the REAL wall clock, while
    # our SourceTimestamps are simulated and up to history_depth in the past. Any
    # period shorter than the history depth erases history as it is written.
    #
    # Both calls below carry a suppression for this same reason: the public wrapper
    # types `period` as plain `timedelta`, but forwards it verbatim to
    # iserver.enable_history_data_change / enable_history_event, whose own signatures
    # accept `timedelta | None` -- which is what actually disables the period-based
    # eviction above. The wrapper's annotation is narrower than what it forwards.
    await server.historize_node_data_change(
        [stream.node for stream in streams],
        period=None,  # type: ignore[arg-type]
        count=0,
    )
    inspection = space.inspection.node
    await server.historize_node_event(inspection, period=None, count=0)  # type: ignore[arg-type]

    # Confirmed against asyncua 2.0.1's own source, not guessed: every event this
    # station triggers reaches HistorySQLite.save_event(), inserts its row correctly
    # (keyed by event.SourceNode, which is preserved), then raises KeyError from the
    # very next line -- self._datachanges_period[event.emitting_node] -- because
    # emitting_node is never S3, no matter which node the event was raised on. The
    # in-process Subscription that delivers events to this historian (the same
    # client-facing Subscription class used for real remote clients, reused here by
    # HistoryManager._create_subscription) reconstructs each event via
    # Event.from_event_fields(), which rebuilds only the fields listed in the
    # event's SelectClauses. emitting_node is asyncua's own internal bookkeeping,
    # never a selectable field, so it resets to the Event() default of
    # ObjectIds.Server on every delivery, and that key was never registered (only
    # the station's NodeId was, by new_historized_event above). This runs
    # unconditionally, before the `if period:` guard, so period=None does not avoid
    # it. The failure is confined to a fire-and-forget task asyncio already reports
    # as "exception was never retrieved" and does not touch the row already
    # committed; LedgerWriter.reconcile polls the historian's own row counts rather
    # than awaiting or gathering these tasks for exactly this reason.
    # _quiet_known_event_history_bug (above) filters just this one exception out of
    # asyncio's default per-task logging.
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(
        lambda lp, ctx: _quiet_known_event_history_bug(lp, ctx, previous_handler)
    )
    return storage
