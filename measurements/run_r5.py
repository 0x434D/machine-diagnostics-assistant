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
    # HistorySQLite opens its aiosqlite connection in .init(), not __init__ -- and
    # server.init() already called .init() on the *default* HistoryManager storage
    # before this one replaces it. set_storage() only swaps the reference, so without
    # this line self._db stays None and the first CREATE TABLE raises AttributeError.
    await storage.init()
    server.iserver.history_manager.set_storage(storage)

    folder = await server.nodes.objects.add_object(idx, "R5")
    # -1.0, not 0.0: OPC UA requires a MonitoredItem to deliver the Node's *current*
    # value the moment it starts Reporting (spec part 4 §5.12.1), so historize below
    # always captures one row per node before this function writes anything. Every
    # write in this probe is >= 0.0 (row * streams + stream), so a negative seed can
    # never equal a real write and get silently coalesced into it -- with 0.0 the
    # first stream's row-0 write did exactly that, replacing the true first sample
    # with the seed's real-wall-clock timestamp and hiding one real write behind it.
    nodes = [
        await folder.add_variable(idx, f"S{i:02d}", -1.0, ua.VariantType.Double)
        for i in range(streams)
    ]
    for node in nodes:
        # Server.historize_node_data_change types `period` as `timedelta`, but forwards
        # it unchanged to HistoryManager.historize_data_change, whose real signature is
        # `timedelta | None` -- None meaning no time-based expiry. Unbounded retention is
        # exactly D9's question, so the call needs the value the public stub does not
        # admit.
        await server.historize_node_data_change(node, period=None, count=0)  # type: ignore[arg-type]

    async with server:
        start_ts = datetime.now(UTC) - timedelta(seconds=rows_per_stream)
        began = time.monotonic()
        written = 0
        for row in range(rows_per_stream):
            sim_ts = start_ts + timedelta(seconds=row)
            for stream, node in enumerate(nodes):
                # The value must differ every write or asyncua's monitored-item
                # filter coalesces it away before the storage layer sees it -- the
                # same defect stations.base.Station.next_takt exists to dodge.
                await node.write_value(
                    ua.DataValue(
                        ua.Variant(
                            float(row * streams + stream), ua.VariantType.Double
                        ),
                        # DataValue types SourceTimestamp as ua.DateTime, a datetime
                        # subclass asyncua's own runtime never constructs -- every
                        # value that reaches here, including this one, is a plain
                        # datetime. That's not merely tolerated: since Python 3.12
                        # deprecated the implicit subclass adapter, sqlite3's binder
                        # matches by exact type, so a real ua.DateTime would raise
                        # "type 'DateTime' is not supported" inside
                        # HistorySQLite.save_node_value's own try/except, where it is
                        # logged, not raised -- and the row vanishes with no visible
                        # error. The annotation is narrower than what storage needs.
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
        # Each node's mandatory initial-value capture (see the -1.0 seed comment
        # above) is a real row in the historian but not one of this probe's writes,
        # so it would otherwise register as a phantom negative "dropped" count and
        # mask true queue-overflow loss underneath it.
        stored -= streams
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
