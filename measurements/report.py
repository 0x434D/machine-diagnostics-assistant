"""Aggregate every M1 measurement into one table, with a verdict per risk.

§12 names four boundary risks and M1 exists to measure them. This renders what was measured
against what was required, and **exits non-zero if any risk has neither a pass nor a
recorded, justified deviation** — so "we never got round to it" cannot pass silently, which
is the failure mode this milestone found six times in other forms.

    uv run --frozen --package simulator python measurements/report.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

REPO = Path(__file__).resolve().parents[1]
MEASUREMENTS = REPO / "measurements"
REPORT = REPO / "docs/superpowers/measurements/2026-09-12-m1-boundary-risks.md"

Verdict = Literal["PASS", "DEVIATION", "FAIL"]


@dataclass
class Check:
    name: str
    measured: str
    required: str
    verdict: Verdict
    note: str = ""


@dataclass
class Risk:
    key: str
    title: str
    restated: str
    checks: list[Check] = field(default_factory=list)

    @property
    def verdict(self) -> Verdict:
        if any(check.verdict == "FAIL" for check in self.checks):
            return "FAIL"
        if any(check.verdict == "DEVIATION" for check in self.checks):
            return "DEVIATION"
        return "PASS"


def _load(name: str) -> dict[str, object]:
    loaded = json.loads((MEASUREMENTS / name).read_text())
    assert isinstance(loaded, dict)
    return loaded


def _verdict(ok: bool) -> Verdict:
    return "PASS" if ok else "FAIL"


def r1() -> Risk:
    data = _load("r1-r2-results.json")
    depths = data["depths"]
    assert isinstance(depths, list)
    deep = depths[-1]
    linearity = float(str(data["linearity"]))

    exact = all(
        stream["read_rows"] == stream["pg_rows"]
        for run in depths
        for stream in run["streams"]
    )
    gaps = sum(int(run["ingest_gaps"]) for run in depths)
    counts = ", ".join(f"{s['stream']} {s['pg_rows']}" for s in deep["streams"])

    return Risk(
        "R1",
        "asyncua history backend at ~20k rows per stream (§12 row 1)",
        "The risk as stated is slow or unreliable backfill. F1-F4 relocate it: the danger is "
        "silent miscounting, not latency.",
        [
            Check(
                "pg_rows == read_rows, every stream, both depths",
                f"exact at 1 h and 33 h ({counts})",
                "exact, or the milestone does not ship",
                _verdict(exact),
                "The criterion that must not be waived. F1 means it does not hold by default: "
                "an unbounded read returns 10,000 of 19,800 and reports success.",
            ),
            Check("ingest_gaps", str(gaps), "0", _verdict(gaps == 0)),
            Check(
                "backfill_wall (33 h)",
                f"{deep['backfill_wall_s']} s",
                "<= 300 s",
                _verdict(float(str(deep["backfill_wall_s"])) <= 300),
            ),
            Check(
                "page_p99 (33 h)",
                f"{deep['page_p99_ms']} ms",
                "<= 10,000 ms",
                _verdict(float(str(deep["page_p99_ms"])) <= 10_000),
            ),
            Check(
                "linearity",
                str(linearity),
                "<= 2.0",
                _verdict(linearity <= 2.0),
                "Below 1.0: the deep run costs less per row than the shallow one, because a "
                "fixed startup cost is amortised over thirty-three times the rows.",
            ),
            Check(
                "catchup_wall",
                "151.3 / 160.7 / 184.9 s over three boots",
                "<= 240 s (restated)",
                "DEVIATION",
                'The plan\'s 180 s was a generous reading of "600x is about 108 s" at 18 h with '
                "flat-art images. Scaling for the corrected 33 h depth alone gives ~198 s "
                "before any render realism, so the threshold was unreachable by construction. "
                "Restated to ~240 s by decision, with render cost deliberately NOT cut to fit: "
                "img_p99 only just crosses MaxBufferSize, which is the sole reason R4 is not "
                "vacuous.",
            ),
            Check(
                "duplicate_rate",
                "0 at both depths",
                "~= pages - windows, absorbed by the upsert",
                "DEVIATION",
                "Zero is not a pass here, it is an untriggered case. Variable windows fit "
                "inside a single page and no row landed on a subdivision midpoint, so F2's "
                "duplicates never arrived. The upsert that absorbs them is covered by unit "
                "test, not by this run. A run that never triggers the case is not evidence "
                "the case is handled.",
            ),
        ],
    )


def r2() -> Risk:
    return Risk(
        "R2",
        "UA-.NETStandard HistoryRead client ergonomics (§12 row 2)",
        "The risk as stated is that the gateway work is larger than estimated. It is, and the "
        "deliverable is the written list of what the SDK does not do for you.",
        [
            Check(
                "HistoryRead helper in the SDK",
                "none at 1.5.378.176",
                "used if present",
                "PASS",
                "HistoryClient belongs to the unreleased 2.0 line. Paging, decoding and "
                "continuation-point release are all ours: ~250 lines in Opc/HistoryBackfill.cs.",
            ),
            Check(
                "continuation points",
                "released on every exit path, cancellation included",
                "no leaks",
                "PASS",
                "A leaked point holds a server-side cursor until the pool refuses further reads.",
            ),
            Check(
                "page size per stream",
                "1,000 for variables, 25 for events",
                "one size, per the plan",
                "DEVIATION",
                "The event stream carries images: a 1,000-row page is ~5.5 MB at a 5% reject "
                "rate against a 4 MiB response limit, and the read fails as "
                "BadEncodingLimitsExceeded. Same shape as the per-signal deadband argument the "
                "plan already makes for TaktTime versus PartCount.",
            ),
        ],
    )


def r3() -> Risk:
    data = _load("r3-matrix.json")
    cells = data["cells"]
    assert isinstance(cells, dict)
    signed = {name: cell for name, cell in cells.items() if name.endswith("/Sign")}
    insecure = {
        name: cell for name, cell in cells.items() if name.endswith("/NoSecurity")
    }

    return Risk(
        "R3",
        "Endpoint URL vs. Docker hostname, and certificate SANs vs. service names (§12 rows 3, 5)",
        "Four client positions against two security modes. The positions differ only in where "
        "the client sits and which name it dials.",
        [
            Check(
                "every Sign cell connects",
                f"{sum(1 for c in signed.values() if c['ok'])}/{len(signed)}",
                "all",
                _verdict(all(c["ok"] for c in signed.values())),
                "Includes the only client that matters in production: UA-.NETStandard inside "
                "field-net, over the service name, with checkDomain on.",
            ),
            Check(
                "every NoSecurity cell is refused",
                f"{sum(1 for c in insecure.values() if not c['ok'])}/{len(insecure)}",
                "all refused",
                _verdict(not any(c["ok"] for c in insecure.values())),
                "The design working rather than a failure: the server advertises Sign only.",
            ),
            Check(
                "manual host configuration",
                "none",
                "none (§14)",
                "PASS",
                "opc.tcp://localhost:4840/plant works because the certificate's IP SAN covers "
                'it, so the /etc/hosts prerequisite is retired and "no manual steps, no fixing '
                'hostnames by hand" holds without qualification.',
            ),
        ],
    )


def r4() -> Risk:
    data = _load("r4-ceiling.json")
    rungs = data["rungs"]
    assert isinstance(rungs, list)
    delivered = [rung for rung in rungs if rung["delivered"]]
    failed = next((rung for rung in rungs if not rung["delivered"]), None)
    img_p99 = 110_419
    ceiling = int(delivered[-1]["actual_bytes"])

    return Risk(
        "R4",
        "Structured events carrying image bytes (§12 row 4)",
        "The risk as stated is that rejects arrive without evidence.",
        [
            Check(
                "ceiling / img_p99",
                f"{ceiling // img_p99}x ({ceiling:,} B delivered, img_p99 {img_p99:,} B)",
                ">= 4x",
                _verdict(ceiling >= 4 * img_p99),
            ),
            Check(
                "chunking exercised at the working size",
                f"img_p99 {img_p99:,} B > MaxBufferSize 65,535 B",
                "crossed, or the measurement is vacuous",
                "PASS",
                "Every reject image crosses a single OPC UA buffer, so the transport mechanism "
                "this risk exists to probe runs on the ordinary path rather than only at the "
                "top of a ladder.",
            ),
            Check(
                "the limit that fires",
                str(failed["limit"]) if failed else "none",
                "MaxByteStringLength or MaxMessageSize, per the plan",
                "DEVIATION",
                "Both are unenforced in asyncua 2.0.1. What stopped delivery was the chunk-count "
                "limit, a different mechanism roughly 26x higher than the byte-string limit the "
                "plan assumed would fire first. The binding end-to-end constraint is therefore "
                "the .NET client's 4 MiB MaxByteStringLength, which leaves 38x headroom at "
                "img_p99 — so nothing is sized from the plant's ceiling.",
            ),
            Check(
                "end-to-end delivery",
                "527 reject images, 109,290-110,486 B, into inspection_images",
                "images reach the database",
                "PASS",
                "Measured on the live path with 21,142 good parts carrying no image row at all, "
                "which is §3.4 holding rather than being asserted.",
            ),
        ],
    )


# Findings that no §12 row predicted. Narrative rather than derived, because the point of
# each is what it means, not what it measures.
UNANTICIPATED = [
    (
        "Three independent silent-truncation modes in one library",
        (
            "asyncua 2.0.1 discards data and reports success in three unrelated places. F1: "
            "`read_raw_history` returns at most 10,000 values regardless of page size, and 33 h at a "
            "6 s takt is 19,800. The write path: the internal subscription queue caps each monitored "
            "item at 10,000 and discards the *oldest*, which destroyed the first 16 h 20 min of a 33 "
            "h generation — precisely the shift the depth exists to guarantee. And event history: "
            "`_get_bounds` caps the SQL at the *client's* page size while a continuation point is "
            "only emitted when the result exceeds the *server's* cap, so a client paging smaller than "
            "that cap gets `None` structurally and stops early. `read_node_history` has the identical "
            "structure, so variables are not immune — they were safe here only because the page size "
            "happened to equal the server's default cap. All three are detectable client-side, which "
            "is the argument for the guard living in the gateway rather than in a request that the "
            'server behave. That distinction is what separates "we worked around a library bug" from '
            '"a gateway pointed at a plant it does not control survives this class of defect", and '
            "the second is what this project claims. "
        ),
    ),
    (
        "The recurring defect shape: the check was not in the path that decided",
        (
            "Six instances at the time of writing, none caught by a linter, a type checker or a "
            "passing test. An "
            "event-field-order test asserted the server's browse order against the constant that defined "
            "it, so a reorder moved both sides together. R3's runner computed `pending` and never "
            "consulted it, so the matrix would have reported PASS with its one load-bearing row red. "
            "A connect probe asked for a security mode and never asserted it got one, so `--security "
            'None` negotiated a *signed* session and exited 0 — which would have put "insecure '
            'connection works" in the matrix while the connection was signed. A truncation guard '
            "fired at 10,000 and could not see a stop at 25, losing 96% of event history on a green "
            "run. `/status` reported the session rather than the pipeline, so it read `backfilling` "
            "forever once backfill finished. And a reconciliation helper named "
            "`IsExplainedByPageBoundaries` reduced to `Pages` once its arithmetic was followed "
            "through. Only mutation found any of them: change the thing the check is supposed to "
            "catch, and watch whether the check fails. "
        ),
    ),
    (
        "A schema is not a guarantee: the gap table nothing ever wrote to",
        (
            "`ingest_gaps` was created in Task 9, read by `/inspection/stats` from Task 12, and "
            "surfaced by the agent as a caveat in Task 13. No code path ever inserted a row. Each "
            "of the two tasks that could have written one left a comment deferring it to the "
            "other, and both comments read as descriptions of work that existed. The result was a "
            "coverage endpoint that reported perfect coverage across any outage, and an agent "
            "caveat that could not fire — §4.4's premise is that a gap marker is what makes "
            "missing data distinguishable from a quiet machine, and for three tasks the system "
            "asserted the premise while making the two indistinguishable. A seventh instance of "
            "the shape above, and the most expensive: the others made a check useless, this one "
            "made an answer confidently wrong. The lesson is narrow and worth stating — a table, "
            "a read path and a renderer are not evidence that anything writes. What would have "
            "caught it is a test that asserts a row appears, which is now three of them. "
        ),
    ),
    (
        "Captured resources outlive the reconnect that replaces them",
        (
            "§1's third authenticity proof failed for four measured runs. Everything visible "
            "pointed away from the cause: the reconnect worked, the state machine moved through "
            "its phases in order, live data resumed, and the gap-closing backfill reported "
            "success having found nothing to close. It found nothing because `HistoryBackfill` "
            "was handed an `ISession` at construction and kept it, while a reconnect against a "
            "restarted plant necessarily builds a new session and disposes the old — so the "
            "backfill was reading a disposed object, which returns nothing and raises nothing. "
            "The same file had the same fault twice over: `KeepAlive` was subscribed on the "
            "session object at connect time, so the replacement arrived with no handler and the "
            "gateway could detect exactly one outage per process lifetime. Neither is an OPC UA "
            "quirk. Both are the general hazard of a long-lived object holding a reference to a "
            "connection that is allowed to be replaced underneath it, and the general fix is to "
            "resolve the connection per use rather than capture it. Worth a line in any review "
            "of M2's nine additional signal streams, each of which will hold one. "
        ),
    ),
    (
        "Test-level verification and wire-level verification are different claims",
        (
            "The clock nodes existed in every pytest run and in no running container, because pytest "
            "builds its own in-process server. The same shape one level up: the check ran, just not "
            "in the path that decided. Worth recording as a process finding rather than a code one. "
        ),
    ),
    (
        "ON CONFLICT evaluates nextval before it detects the conflict",
        (
            "Both DO UPDATE and DO NOTHING advance the sequence on a row that already exists. A "
            "station upsert running once per record exhausted SMALLSERIAL's 32,767 values inside a "
            "single backfill, against a table holding one row, and every write then failed. `INSERT "
            "... SELECT ... WHERE NOT EXISTS` produces no row at all when the station exists, so "
            "nextval never runs. Postgres behaviour rather than an asyncua one, and the only defect "
            "in this list that the local queue's retry made loud instead of silent: nothing was "
            "acked, the queue grew, and /status showed it growing. "
        ),
    ),
    (
        "§3.2's clock paragraph is wrong in its numbers and in its justification",
        (
            "History depth is 33 h, not 18 h. §3.2 claims 18 h puts the previous night shift fully "
            "inside history; that holds only for a morning boot. The worst case is a 05:00 boot "
            "*inside* a running night shift, where the last completed one began 33 h earlier — 24 h "
            "of day-gap plus a 9 h autumn fall-back night, supremum 1 day 8:59:59.999999 on "
            "2026-10-25, found by probing 366 day-boundaries. The plan's own 26 h estimate came from "
            "sampling hourly, which never lands on the sawtooth's right edge. Catch-up is 700x, not "
            '600x, and the "about 108 s" figure measured 151.3-184.9 s. '
        ),
    ),
]


def render(risks: list[Risk]) -> str:
    lines = [
        "# M1 boundary risks — what was measured",
        "",
        (
            "Generated by `measurements/report.py`. Every number here was measured on this "
            "repository; none is carried over from the plan's estimates."
        ),
        "",
        "| Risk | Verdict |",
        "|---|---|",
    ]
    lines += [f"| {risk.key} — {risk.title} | **{risk.verdict}** |" for risk in risks]

    for risk in risks:
        lines += ["", f"## {risk.key} — {risk.title}", "", risk.restated, ""]
        lines += ["| Check | Measured | Required | Verdict |", "|---|---|---|---|"]
        lines += [
            f"| {c.name} | {c.measured} | {c.required} | {c.verdict} |"
            for c in risk.checks
        ]
        notes = [c for c in risk.checks if c.note]
        if notes:
            lines.append("")
            lines += [f"**{c.name}.** {c.note}" + "\n" for c in notes]

    lines += ["", "## What M1 found that §12 did not predict", ""]
    for title, body in UNANTICIPATED:
        lines += [f"### {title}", "", body.strip(), ""]

    return "\n".join(lines) + "\n"


def main() -> None:
    risks = [r1(), r2(), r3(), r4()]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(render(risks))

    for risk in risks:
        print(f"{risk.key:<4} {risk.verdict}")

    failed = [risk.key for risk in risks if risk.verdict == "FAIL"]
    if failed:
        print(f"\nFAIL: {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)
    print(f"\nwrote {REPORT.relative_to(REPO)}")


if __name__ == "__main__":
    main()
