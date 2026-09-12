"""Historian wiring, with the traps that bite here spelled out."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from asyncua import Server, ua
from asyncua.server.history_sql import HistorySQLite

from simulator.address_space import AddressSpace


@dataclass
class Ledger:
    """What the generator believes it wrote. R1 reconciles this against the
    historian, against what HistoryRead returns, and against Postgres."""

    takt: int = 0
    part_count: int = 0
    events: int = 0
    images: int = 0
    # Not in the brief's interface list; kept because R1 also reconciles storage
    # volume against Postgres, and a reject-image byte total is the one number here
    # that a row count alone can't stand in for.
    image_bytes: int = 0


_EVENT_HISTORY_BUG_KEY = (ua.NodeId(ua.Int32(ua.ObjectIds.Server)),)
"""The exact KeyError asyncio.Event.from_event_fields' reconstruction (see below)
always raises from inside HistorySQLite.save_event's fire-and-forget task -- used
to filter only that one confirmed, understood exception out of the default
per-task logging, below."""


def _quiet_known_event_history_bug(
    loop: asyncio.AbstractEventLoop,
    context: dict[str, object],
    fallback: Callable[[asyncio.AbstractEventLoop, dict[str, object]], object] | None,
) -> None:
    """Filters one specific, confirmed asyncua bug out of asyncio's default
    per-task exception logging; everything else still gets the full report.

    Every event this station triggers hits this exact KeyError inside
    HistorySQLite.save_event's own fire-and-forget task (see the long comment on
    historize_node_event below for the mechanism) -- once per event, drowning out
    anything else `make check` needs to show, and training readers to skim past
    tracebacks in a project whose whole thesis is not doing that. Matched on the
    exact KeyError value this one bug produces, not on exception type alone, so a
    genuinely new failure in the same task still surfaces normally.
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
    storage = HistorySQLite(str(db_path), max_history_data_response_size=page_size)

    # HistoryManager.init() already ran inside Server.init() against the default
    # in-memory HistoryDict, so replacing the storage means initialising it here.
    await storage.init()
    server.iserver.history_manager.set_storage(storage)

    # OPC UA's own subscribe_data_change delivers an initial-value notification at
    # subscribe time, using whatever value and SourceTimestamp the node currently
    # holds -- unavoidably becoming each variable's first historised row. Left as
    # the wall-clock timestamp add_variable gave it, that row would violate the
    # project invariant that SourceTimestamp is always simulated, and Critical 3's
    # ledger-vs-historian reconciliation would see a row nobody counted. Priming
    # both variables with a deliberate SourceTimestamp one takt before history_start
    # -- outside every window generate_history will ever backfill -- fixes the
    # timestamp and lets the ledger count it truthfully (below), rather than
    # excluding a row that is, after this, a real and correctly timestamped one.
    priming_value = ua.DataValue(
        ua.Variant(0.0, ua.VariantType.Double),
        SourceTimestamp=priming_source_timestamp,  # type: ignore[arg-type]
    )
    await space.takt.write_value(priming_value)
    ledger.takt += 1
    await space.part_count.write_value(
        ua.DataValue(
            ua.Variant(0, ua.VariantType.UInt32),
            SourceTimestamp=priming_source_timestamp,  # type: ignore[arg-type]
        )
    )
    ledger.part_count += 1

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
        [space.takt, space.part_count],
        period=None,  # type: ignore[arg-type]
        count=0,
    )
    await server.historize_node_event(space.s3, period=None, count=0)  # type: ignore[arg-type]

    # Confirmed against asyncua 2.0.1's own source, not guessed: every event this
    # station triggers reaches HistorySQLite.save_event(), inserts its row correctly
    # (keyed by event.SourceNode, which is preserved), then raises KeyError from the
    # very next line -- self._datachanges_period[event.emitting_node] -- because
    # emitting_node is never s3, no matter which node the event was raised on. The
    # in-process Subscription that delivers events to this historian (the same
    # client-facing Subscription class used for real remote clients, reused here by
    # HistoryManager._create_subscription) reconstructs each event via
    # Event.from_event_fields(), which rebuilds only the fields listed in the
    # event's SelectClauses. emitting_node is asyncua's own internal bookkeeping,
    # never a selectable field, so it resets to the Event() default of
    # ObjectIds.Server on every delivery, and that key was never registered (only
    # space.s3's NodeId was, by new_historized_event above). This runs
    # unconditionally, before the `if period:` guard, so period=None does not avoid
    # it. The failure is confined to a fire-and-forget task asyncio already reports
    # as "exception was never retrieved" and does not touch the row already
    # committed; station_s3._reconcile_with_historian polls the historian's own row
    # counts rather than awaiting or gathering these tasks for exactly this reason.
    # _quiet_known_event_history_bug (above) filters just this one exception out of
    # asyncio's default per-task logging.
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(
        lambda lp, ctx: _quiet_known_event_history_bug(lp, ctx, previous_handler)
    )
    return storage
