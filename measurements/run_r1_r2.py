"""R1 and R2: does the backfill count correctly, and how much of it is ours to write.

R1's risk as stated is "backfill slow or unreliable". F1-F4 relocate it: the danger is
silent miscounting, not latency, so the criterion that matters is pg_rows == plant_rows
exactly. Run against a plant that is already up and live.

    uv run --frozen --package simulator python measurements/run_r1_r2.py
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import TypedDict

REPO = Path(__file__).resolve().parents[1]
GATEWAY_IMAGE = "machine-agent/edge-gateway:dev"
POSTGRES_IMAGE = (
    "postgres:17-bookworm@sha256:"
    "051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0"
)
ENDPOINT = "opc.tcp://line-simulator:4840/plant"

# THIS IS M1's RUNNER AND IT DOES NOT RUN AGAINST AN M2a GATEWAY. Deliberate; see below.
#
# M2a Task 10 qualified backfill_windows.stream by station code ("S2.TaktTime"), because
# the table is UNIQUE (from_ts, to_ts, stream) and four stations writing a bare "TaktTime"
# overwrote each other. Two things below no longer hold:
#
#   * `WHERE stream = 'TaktTime'` matches no ledger row, so read_rows comes back 0 and
#     report.py's `read_rows == pg_rows` check reads FAIL on a run that was fine;
#   * `WHERE s.signal = 'TaktTime'` sums all four stations into one pg_rows.
#
# It fails in the safe direction, and Task 12 looked at repairing it and decided not to.
# Re-deriving the per-signal routing here would be a second copy of Reconciler.StoredAsync
# -- five tables, a settled-transitions view and a raw_events DISTINCT -- and the way a
# second copy goes wrong is to report a clean R1 while the gateway stores something else.
# The right shape is to call the gateway's own /reconcile over an explicit window, and that
# moves R1's criterion from `read_rows == pg_rows` to `lost == 0`, because at M2a's page
# counts F2's page-boundary duplicates are expected rather than loss. Changing what R1
# passes on is the spec owner's call.
#
# A second reason it does not run today, found by running `make verify`: _run_gateway below
# starts r1-gw without mounting diagnostics/gateway/Gateway/config/signals.json, and Task 9
# made that policy mandatory -- the container exits 139 on its first line. Whoever repairs the
# queries has to add the mount too, the same one test_authenticity.py's fixture now carries.
#
# The claim itself is not waiting on any of that: measurements/authenticity/README.md records
# it measured on a full 33 h boot -- 26 streams, 402,044 rows read, 402,044 stored, 0 lost --
# and gives the two commands. What this file and r1-r2-results.json still describe is M1.
STREAMS = ("TaktTime", "PartCount", "InspectionResult")

# One hour at a 6 s takt is 600 rows per stream; the full depth is 33 h.
DEPTHS_HOURS = (1, 33)
BACKFILL_TIMEOUT_S = 600


class StreamResult(TypedDict):
    stream: str
    read_rows: int
    pg_rows: int
    duplicate_rate: int
    pages: int
    windows: int


class DepthResult(TypedDict):
    depth_hours: int
    backfill_wall_s: float
    page_p50_ms: float
    page_p99_ms: float
    ingest_gaps: int
    streams: list[StreamResult]


def _sh(*args: str) -> str:
    return subprocess.run(
        args, capture_output=True, text=True, check=False
    ).stdout.strip()


def _psql(sql: str) -> str:
    return _sh(
        "docker", "exec", "r1-pg", "psql", "-U", "postgres", "-t", "-A", "-c", sql
    )


def _start_postgres() -> None:
    _sh("docker", "rm", "-f", "r1-pg")
    _sh(
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        "r1-pg",
        "-e",
        "POSTGRES_PASSWORD=r1",
        "-p",
        "15433:5432",
        POSTGRES_IMAGE,
    )
    for _ in range(60):
        if _psql("SELECT 1") == "1":
            return
        time.sleep(1)
    raise RuntimeError("postgres did not come up")


def _run_gateway(depth_hours: int, queue: Path) -> float:
    """Starts the gateway and returns wall seconds from start to backfill complete."""
    _sh("docker", "rm", "-f", "r1-gw")
    queue.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    _sh(
        "docker",
        "run",
        "-d",
        "--name",
        "r1-gw",
        "--network",
        "field-net",
        "--add-host=host.docker.internal:host-gateway",
        "-v",
        f"{REPO / 'pki'}:/pki:ro",
        "-v",
        f"{queue}:/queue",
        "--user",
        f"{Path.home().stat().st_uid}:{Path.home().stat().st_gid}",
        "-p",
        "18081:8080",
        "-e",
        f"GATEWAY_HISTORY_DEPTH_HOURS={depth_hours}",
        "-e",
        f"GATEWAY_ENDPOINT_URL={ENDPOINT}",
        "-e",
        "GATEWAY_POSTGRES=Host=host.docker.internal;Port=15433;Username=postgres;"
        "Password=r1;Database=postgres",
        GATEWAY_IMAGE,
    )

    deadline = started + BACKFILL_TIMEOUT_S
    while time.monotonic() < deadline:
        status = _sh("curl", "-s", "--max-time", "5", "http://localhost:18081/status")
        if status:
            try:
                parsed = json.loads(status)
            except json.JSONDecodeError:
                parsed = {}
            if parsed.get("backfillProgress") == 1 and parsed.get("queueDepth") == 0:
                return time.monotonic() - started
        time.sleep(2)
    raise RuntimeError(f"backfill did not complete within {BACKFILL_TIMEOUT_S}s")


def _streams() -> list[StreamResult]:
    results: list[StreamResult] = []
    for stream in STREAMS:
        ledger = _psql(
            "SELECT coalesce(sum(rows_returned),0), coalesce(sum(pages),0), count(*) "
            f"FROM backfill_windows WHERE stream = '{stream}'"
        ).split("|")
        stored_sql = (
            "SELECT count(*) FROM inspection_results r, "
            "(SELECT min(from_ts) f, max(to_ts) t FROM backfill_windows) w "
            "WHERE r.source_ts >= w.f AND r.source_ts < w.t"
            if stream == "InspectionResult"
            else "SELECT count(*) FROM signals s, "
            "(SELECT min(from_ts) f, max(to_ts) t FROM backfill_windows) w "
            f"WHERE s.signal = '{stream}' AND s.source_ts >= w.f AND s.source_ts < w.t"
        )
        read_rows, pages, windows = (int(value) for value in ledger)
        pg_rows = int(_psql(stored_sql))
        results.append(
            StreamResult(
                stream=stream,
                read_rows=read_rows,
                pg_rows=pg_rows,
                duplicate_rate=read_rows - pg_rows,
                pages=pages,
                windows=windows,
            )
        )
    return results


def _page_latencies() -> tuple[float, float]:
    """Per-HistoryRead-call latency, derived from each window's duration and page count."""
    raw = _psql(
        "SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY duration_ms::float/greatest(pages,1)),"
        " percentile_disc(0.99) WITHIN GROUP (ORDER BY duration_ms::float/greatest(pages,1))"
        " FROM backfill_windows"
    )
    p50, p99 = raw.split("|")
    return float(p50), float(p99)


def measure(depth_hours: int) -> DepthResult:
    _start_postgres()
    wall = _run_gateway(depth_hours, REPO / ".r1-queue" / str(depth_hours))
    p50, p99 = _page_latencies()
    result = DepthResult(
        depth_hours=depth_hours,
        backfill_wall_s=round(wall, 1),
        page_p50_ms=p50,
        page_p99_ms=p99,
        ingest_gaps=int(_psql("SELECT count(*) FROM ingest_gaps")),
        streams=_streams(),
    )
    _sh("docker", "rm", "-f", "r1-gw")
    return result


def main() -> None:
    subprocess.run(
        [
            "docker",
            "build",
            "-q",
            "-f",
            "diagnostics/gateway/Dockerfile",
            "-t",
            GATEWAY_IMAGE,
            ".",
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
    )

    depths = [measure(hours) for hours in DEPTHS_HOURS]
    per_row = [
        run["backfill_wall_s"] / max(sum(s["pg_rows"] for s in run["streams"]), 1)
        for run in depths
    ]
    # Superlinear backfill shows up as the deep run costing more per row than the shallow one.
    linearity = round(per_row[-1] / per_row[0], 2) if per_row[0] else 0.0

    out = REPO / "measurements" / "r1-r2-results.json"
    out.write_text(
        json.dumps({"depths": depths, "linearity": linearity}, indent=2) + "\n"
    )
    print(json.dumps({"linearity": linearity, "depths": depths}, indent=2))
    _sh("docker", "rm", "-f", "r1-pg")


if __name__ == "__main__":
    main()
