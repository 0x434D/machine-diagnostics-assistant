"""§3.6's ground-truth log: what was injected, what should follow, and what is really
wrong with every part.

**This file is why every evaluation number in the project can mean anything, and it is
also the one file that can make them all worthless.** It lives on a volume no
diagnostics container mounts (`plant/compose.yml`, and `test_compose_invariants` is what
keeps it that way). The moment anything on the other side of the boundary can read it,
the analysis is being graded against something it could have looked at.

**It records consequences, never causes.** Each injection carries the observable
consequences the scenario claims for it -- "S2, then S3, then S4, Suspended and starved,
in that order, within 180 s" -- and Task 7 asserts that sequence appears in
`state_changes`. It does **not** record what an analysis should conclude. Writing the
expected diagnosis here would mean the log had been written by the reasoning M7 later
grades against it, and every number that came out would be circular. M3 infers a cause;
M7 scores the inference; this file is neither.

**Every timestamp is simulated time.** A fault is injected at a simulated instant and
recorded at that instant, the same `SourceTimestamp` the whole of the analysis runs on.
The wall clock appears nowhere in this file, so two runs of one scenario produce the
same bytes -- which is §3.6's determinism claim, and
`test_a_run_is_reproducible_byte_for_byte_from_its_seed` is what checks it.

One JSON object per line, three kinds, in the order they can be known: the run, then its
injections, then a part for every part the line inspected. JSONL rather than one document
because the third kind arrives for tens of thousands of parts over the whole run, and a
document has to be complete before it is valid.
"""

from __future__ import annotations

import json
import zlib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Final, Self

from simulator.clock import SimulatedClock
from simulator.config import Settings
from simulator.inspection_client import InspectionClient
from simulator.scenarios import Consequence, Injection, Scenario
from simulator.stations.base import PartOutcome, ProduceFn

RUN: Final = "run"
INJECTION: Final = "injection"
PART: Final = "part"
"""The three record kinds, in the `record` field of every line. A reader dispatches on
this; nothing is inferred from which fields are present."""

NO_SCENARIO: Final = 0
"""What `scenario` carries on a clean run. `0` rather than absent, because a log with no
scenario field would read the same as one written by a version that did not have the
field -- and "this run was clean" is a claim worth being able to make."""


def run_id_for(settings: Settings, clock: SimulatedClock, scenario: int) -> str:
    """This run's identity, derived from what determines it rather than drawn.

    §3.6's claim is that the same seed plus the same scenario reproduces the run exactly,
    so what identifies a run is exactly the seed, the scenario and the clock the offsets
    are placed on. A random id would make two identical runs look like two different ones
    and would make the byte-identity claim above uncheckable at all.

    crc32, never `hash()`: `str.__hash__` is salted per interpreter process
    (PYTHONHASHSEED), so a hash-derived id is stable within one boot and different on the
    next -- the same defect this repository has shipped once already. Thirty-two bits is
    an identifier for a run on one machine and not a universal one, and the line it
    labels carries the seed, the scenario and the clock beside it either way.
    """
    key = (
        f"{settings.seed}:{scenario}:{clock.history_start.isoformat()}:"
        f"{clock.history_depth.total_seconds()}"
    )
    return f"{zlib.crc32(key.encode()):08x}"


class GroundTruthLog:
    """The open log for one run. Append-only, flushed per record.

    Flushed rather than buffered, because the run has no end: the plant generates
    history, then produces live for as long as the container is up, and a log whose last
    minutes were still in a buffer is a log that is wrong about exactly the part someone
    is looking at. Measured at the production depth: 59 ms for 19,800 part records
    against 37 ms unflushed, so the whole of what flushing costs is 22 ms inside a 180 s
    catch-up budget.

    A context manager, and `server.main` uses it as one. `close` exists separately
    because the plant's own entry point holds the log for the whole process lifetime.
    """

    def __init__(self, path: Path, run_id: str) -> None:
        """Opens `path` for appending, creating its parent directory if the volume is
        empty. Raises OSError if the volume is not writable, which is a misconfigured
        mount and must not be survivable -- a run with no ground truth is a run nothing
        downstream can ever be scored against.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._file: IO[str] = path.open("a", encoding="utf-8")

    @property
    def run_id(self) -> str:
        return self._run_id

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._file.close()

    def record_run(
        self, settings: Settings, clock: SimulatedClock, scenario: Scenario | None
    ) -> None:
        """The header: run id, seed, clock configuration, and which scenario this is.

        `carrier_count` is here beside the seed because it is the one setting that
        changes what the seed means: the carrier qualities are normalised across the pool
        (`noise.NoiseFloor.carrier_quality`), so changing the count redraws every one of
        them and a log that carried only the seed would claim reproducibility it does not
        have.
        """
        self._write(
            {
                "record": RUN,
                "run_id": self._run_id,
                "seed": settings.seed,
                "carrier_count": settings.carrier_count,
                "scenario": NO_SCENARIO if scenario is None else scenario.number,
                "scenario_name": "" if scenario is None else scenario.name,
                "note": "" if scenario is None else scenario.note,
                "clock": {
                    "history_start": clock.history_start.isoformat(),
                    "history_depth_seconds": clock.history_depth.total_seconds(),
                    "catchup_speed": clock.catchup_speed,
                },
            }
        )

    def record_injection(self, injection: Injection, origin: datetime) -> None:
        """One fault, at the simulated instants it fires and is repaired, with the
        consequences it is expected to produce.

        `at` and `until` are absolute instants here and offsets in `faults.Fault`. The
        offset is what makes a scenario runnable more than once; the instant is what
        makes the log comparable to `state_changes` and `inspection_results`, which carry
        nothing but instants. Both spellings of one fact, and this is the only place they
        meet.
        """
        fault = injection.fault
        self._write(
            {
                "record": INJECTION,
                "run_id": self._run_id,
                "kind": str(fault.kind),
                "at": (origin + fault.at).isoformat(),
                "until": None
                if fault.until is None
                else (origin + fault.until).isoformat(),
                "params": dict(fault.params),
                "consequences": [_consequence(item) for item in injection.consequences],
            }
        )

    def record_part(
        self,
        part_id: str,
        carrier_id: int,
        at: datetime,
        defects: Sequence[tuple[int, str]],
    ) -> None:
        """What is genuinely wrong with one part, as the plant declared it.

        The plant's own truth and not the classifier's opinion, which is the whole reason
        this is written beside the line: §3.6 wants it so that false accepts and false
        rejects can be scored, and a log that recorded what the vision system said would
        score the classifier against itself.

        **Each defect carries the lane its component came from**, which is the one fact
        about a defect that exists nowhere else: `truth_for` drops the lane because the
        classifier scores a class and not a component, and every assembly draws from both
        lanes, so no part record downstream can ever recover it. §3.5's scenario 5 is
        "clean lane 2", and without this a later milestone grading that answer would be
        grading a guess against something nothing wrote down. Not deduplicated by class
        for the same reason: two lanes both carrying `scratch` is two defects on one part,
        even though the vision system can only ever see one.

        The carrier is here because §3.5's scenario 4 concentrates on one, so "which
        carrier was this part riding" is part of the truth rather than something to be
        recovered by joining two tables afterwards.
        """
        self._write(
            {
                "record": PART,
                "run_id": self._run_id,
                "part_id": part_id,
                "carrier_id": carrier_id,
                "at": at.isoformat(),
                "defects": [{"lane": lane, "class": name} for lane, name in defects],
            }
        )

    def _write(self, record: dict[str, object]) -> None:
        # `sort_keys` so that the bytes depend on the facts and not on the order this
        # module happens to build a dict in -- §3.6's byte-identity claim would otherwise
        # be a claim about a refactor as much as about the plant.
        self._file.write(json.dumps(record, sort_keys=True) + "\n")
        self._file.flush()


def _consequence(item: Consequence) -> dict[str, object]:
    return {
        "observable": item.observable,
        "expect": item.expect,
        "subjects": list(item.subjects),
        "scope": item.scope,
        "within_seconds": item.within_seconds,
    }


def open_log(
    path: Path, settings: Settings, clock: SimulatedClock, scenario: Scenario | None
) -> GroundTruthLog:
    """Open the log for a run and write everything that is known before it starts.

    One function rather than a header call and a loop at every call site, because
    "**every** injection writes to the ground-truth log" is the milestone's rule and a
    caller that wrote the header and forgot the injections would produce a log that looks
    complete. `server.main` and the proofs both come through here.
    """
    log = GroundTruthLog(path, run_id_for(settings, clock, _number(scenario)))
    log.record_run(settings, clock, scenario)
    if scenario is not None:
        for injection in scenario.injections:
            log.record_injection(injection, clock.history_start)
    return log


def _number(scenario: Scenario | None) -> int:
    return NO_SCENARIO if scenario is None else scenario.number


def recording(log: GroundTruthLog, client: InspectionClient) -> ProduceFn:
    """`client.produce`, with every part's true defect state written to `log` first.

    **Here rather than inside `InspectionClient`, and the direction of the dependency is
    the reason.** The client renders, declares truth down the side channel and asks the
    inspection service for a verdict; it has no business knowing that a log exists, and
    an edge from it to this module is a cycle -- this module reads `scenarios`, which
    reads the client's own copy of the defect vocabulary. The same shape as
    `hmi.RecentParts.watching`, which wraps the same call for the same reason.

    The truth is drawn twice per part, once here and once inside `produce`. Both are
    pure functions of their arguments -- and of the same draws, since `truth_for` is
    `truth_by_lane` with the lane dropped -- so the two answers agree by construction, and
    the alternative -- returning the truth alongside the verdict -- would put ground truth
    inside the object that travels to S3, into the OPC UA event and onto the HMI's strip.
    That is the one thing §4.5 forbids, and a duplicated draw of twelve uniforms is not a
    price worth paying to avoid.

    Recorded **before** the request, so an inspection service that answered an error
    leaves a part in the log the plant had already decided about, rather than a gap.
    """

    async def recorded(
        part_id: str, carrier_id: int, joining_work: float, at: datetime
    ) -> PartOutcome:
        log.record_part(
            part_id,
            carrier_id,
            at,
            client.truth_by_lane(part_id, carrier_id, joining_work, at),
        )
        return await client.produce(part_id, carrier_id, joining_work, at)

    return recorded
