"""Historian wiring, with the two traps that bite here spelled out."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from asyncua import Server
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
    image_bytes: int = 0


async def attach_historian(
    server: Server, space: AddressSpace, db_path: Path, page_size: int
) -> HistorySQLite:
    storage = HistorySQLite(str(db_path), max_history_data_response_size=page_size)

    # HistoryManager.init() already ran inside Server.init() against the default
    # in-memory HistoryDict, so replacing the storage means initialising it here.
    await storage.init()
    server.iserver.history_manager.set_storage(storage)

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
    # committed; station_s3._await_historian_settle polls task.done() rather than
    # awaiting or gathering these tasks for exactly this reason.
    return storage
