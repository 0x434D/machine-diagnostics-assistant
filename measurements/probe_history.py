"""Reproduces pre-flight F1 (the 10,000 ceiling) and F2 (page-boundary duplicates).

The pre-flight numbers were taken on Python 3.14. Task 10's whole design — bounded
windows plus a reconciled count per window — rests on them, so they are confirmed on the
pinned 3.13 before anything is built against them.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta

from asyncua import Client, Server, ua
from asyncua.server.history_sql import HistorySQLite

# (values written, storage page size, port). The first shows duplicates; the last two
# bracket the ceiling from below and above.
CASES: tuple[tuple[int, int, int], ...] = (
    (3000, 500, 48420),
    (10800, 1000, 48421),
    (12000, 1000, 48422),
)

TAKT_SECONDS = 6


async def probe(written: int, page: int, port: int) -> int:
    """Write `written` historised values, then read them all back. Returns rows returned."""
    directory = tempfile.mkdtemp()
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{port}/probe")
    idx = await server.register_namespace("probe")
    station = await server.nodes.objects.add_object(idx, "S3")
    takt = await station.add_variable(idx, "TaktTime", 6.0)

    storage = HistorySQLite(
        os.path.join(directory, "h.db"), max_history_data_response_size=page
    )
    await storage.init()
    server.iserver.history_manager.set_storage(storage)

    base = datetime.now(UTC) - timedelta(hours=18)
    async with server:
        # period=None means "keep everything"; asyncua's own signature types it as
        # timedelta while its runtime accepts and documents None.
        await server.historize_node_data_change(takt, period=None, count=0)  # type: ignore[arg-type]
        for i in range(written):
            await takt.write_value(
                ua.DataValue(
                    ua.Variant(6.0 + i * 0.001, ua.VariantType.Double),
                    # DataValue types this as ua.DateTime, a datetime subclass asyncua's
                    # runtime never constructs -- it assigns plain datetimes throughout.
                    # Same suppression and same reason as simulator.address_space.write_at.
                    SourceTimestamp=base + timedelta(seconds=TAKT_SECONDS * i),  # type: ignore[arg-type]
                )
            )
        await asyncio.sleep(2)
        async with Client(f"opc.tcp://127.0.0.1:{port}/probe") as client:
            rows = await client.get_node(takt.nodeid).read_raw_history(
                base - timedelta(minutes=1),
                datetime.now(UTC) + timedelta(days=1),
                0,
            )

    await storage.stop()
    return len(rows)


async def main() -> None:
    print(f"probe_history on python {sys.version.split()[0]}")
    for written, page, port in CASES:
        returned = await probe(written, page, port)
        delta = returned - written
        verdict = "duplicates" if delta > 0 else ("TRUNCATED" if delta < 0 else "exact")
        print(
            f"  written={written:6d} page={page:5d} -> returned={returned:6d}  {verdict}"
        )


if __name__ == "__main__":
    asyncio.run(main())
