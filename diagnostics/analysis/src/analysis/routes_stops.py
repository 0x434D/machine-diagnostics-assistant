"""§5.3's `/stops` and `/stops/{id}`: the stops, and the derivation behind one of them.

`stops.py` finds a stop from the absence of output and `propagation.py` explains it from the
state timeline. Both are pure and both are already tested; this module is the wiring — it
reads the rows, hands them over, and shapes what comes back so a UI can draw the timeline as
a Gantt with the chain overlaid on it.

**The id is derived, never positional.** §6.5 verifies every cited id against the database,
so an id that meant "the third stop in the window you asked about" would resolve to a
different stop the moment the window moved. A stop's identity is the instant the line
stopped producing, and that is what `stop_id` encodes and `/stops/{id}` reads back.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Final

from fastapi import APIRouter, HTTPException

from analysis import queries, windows
from analysis.config import Settings
from analysis.db import connection
from analysis.dependencies import NowDep, SettingsDep, WindowDep
from analysis.models import (
    Derivation,
    DerivationLink,
    Stop,
    StopDetail,
    StopList,
    Unexplained,
    Window,
)
from analysis.propagation import (
    EXECUTE_STATE,
    BufferSample,
    ChainLink,
    LineTopology,
    StationEpisode,
    Termination,
    derive_chain,
)
from analysis.routes_coverage import coverage_of
from analysis.stops import detect_stops

router = APIRouter()

_ONE_TICK: Final = timedelta(microseconds=1)
"""Postgres's own resolution for a `timestamptz`, which is what makes a half-open read one
tick wider exactly equivalent to an inclusive one."""

_STOP_ID_PREFIX: Final = "stop-"
_STOP_ID_WRITE: Final = "%Y%m%dT%H%M%S.%fZ"
"""How a stop id's instant is written. Read back by the `%z` form inlined in
`parse_stop_id`, which is not the same string: `strptime` accepts `Z` for `%z` and returns
an aware instant, while `strftime` renders that same offset as `+0000`. The pattern below is
what holds the two spellings to one shape.
"""

_STOP_ID_PATTERN: Final = re.compile(r"\d{8}T\d{6}\.\d{6}Z")
"""The shape of a stop id's instant: UTC, to the microsecond, in basic ISO-8601.

To the microsecond because that is Postgres's own resolution for a `timestamptz`, so the id
round-trips to exactly the row it names rather than to something within a second of it. The
pattern is a shape test only — a date it accepts can still not exist, which `parse_stop_id`
is where that is found out.
"""


def stop_id(from_ts: datetime) -> str:
    """The stable id of the stop that began at `from_ts`.

    Derived from the instant and nothing else, so two windows that both contain this stop
    cite it identically and a citation survives the window it was found in (§6.5).
    """
    return _STOP_ID_PREFIX + from_ts.astimezone(UTC).strftime(_STOP_ID_WRITE)


def parse_stop_id(candidate: str) -> datetime | None:
    """The instant a stop id names, or None when it names none."""
    if not candidate.startswith(_STOP_ID_PREFIX):
        return None
    instant = candidate.removeprefix(_STOP_ID_PREFIX)
    if _STOP_ID_PATTERN.fullmatch(instant) is None:
        return None
    try:
        return datetime.strptime(instant, "%Y%m%dT%H%M%S.%f%z")
    except ValueError:
        # A shape the pattern accepts and the calendar does not — the 31st of February, an
        # hour of 61. Specifically recoverable and specifically recovered: the value came
        # out of a URL, and the recovery is the None this function already returns for
        # anything unparseable, which the route turns into a 422.
        return None


@router.get("/stops", operation_id="listStops")
def list_stops(window: WindowDep, settings: SettingsDep) -> StopList:
    """Every stop in the window, with a duration and a category for each.

    Coverage rides along because this endpoint's answer is a claim about *absence* — no part
    left S4 — and an ingest gap is an absence that looks exactly the same from here. Without
    it, a window in which the gateway was down reports the outage as the line's stop.

    The state history is read once for the whole window and every stop's chain is derived
    from the same rows, rather than one read per stop: a shift with forty stops would
    otherwise be forty passes over the same timeline.
    """
    with connection(settings) as conn:
        detection = detect_stops(
            queries.output_instants(conn, window),
            window,
            micro_stop_threshold=settings.micro_stop_threshold,
            interruption_floor=settings.interruption_floor,
        )
        kept = detection.stops[: settings.stop_limit]
        history_from, history_to = queries.history_bounds(
            window, settings.propagation_history
        )
        episodes = queries.state_episodes(conn, history_from, history_to)
        levels = queries.buffer_samples(conn, history_from, history_to)
        topology = queries.topology(conn)
        coverage = coverage_of(conn, window)
        # The part that left last before the window opened, which is where a stop already
        # running when it opened actually began. Anchoring on it rather than on the window's
        # edge is what makes such a stop's id the same string the window after it would
        # cite, and the same one /stops/{id} returns as canonical.
        earlier_output = queries.output_at_or_before(conn, window.from_ts)

    return StopList(
        window=Window.of(window),
        coverage=coverage,
        stops=[
            Stop(
                id=_identifier(
                    stop.started_before_window, stop.from_ts, earlier_output
                ),
                from_ts=stop.from_ts,
                to_ts=stop.to_ts,
                duration_seconds=stop.duration_seconds,
                started_before_window=stop.started_before_window,
                open_at_window_end=stop.open_at_window_end,
                category=_derivation(
                    episodes, levels, topology, stop.from_ts, stop.to_ts, settings
                ).category,
            )
            for stop in kept
        ],
        micro_stops=detection.micro_stops,
        micro_stop_threshold_seconds=settings.micro_stop_threshold_seconds,
        truncated=len(detection.stops) > len(kept),
    )


@router.get("/stops/{identifier}", operation_id="getStop")
def get_stop(identifier: str, settings: SettingsDep, now: NowDep) -> StopDetail:
    """One stop, the state timeline around it, and §5.4's chain as a visible derivation.

    **The stop is rebuilt from the database, not from a window.** The id names the instant
    the line stopped producing; the stop is that instant to the next part out, and both ends
    come from `part_dispositions`. So the same id resolves to the same stop whatever window
    it was first cited from — which is what §6.5 means by an id that verifies.

    An id landing inside an interruption rather than at its start resolves to the whole
    interruption, and the `id` in the response is the canonical one. That is not a
    correction of the caller: a stop that had already begun when a window opened is reported
    at the window's edge by `/stops`, and this is where its true beginning is found.

    404 when no interruption at that instant is long enough to be a stop, and 422 when the
    id is not an id. §6.5 needs those to be different answers, and neither is an empty
    result.
    """
    began_at = parse_stop_id(identifier)
    if began_at is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{identifier!r} is not a stop id; the shape is "
                f"{_STOP_ID_PREFIX}YYYYMMDDTHHMMSS.ffffffZ"
            ),
        )

    with connection(settings) as conn:
        # The last part out at or before the cited instant is the stop's true start. An id
        # taken from a stop that was already running when its window opened carries the
        # window's edge, and this is what walks it back to the part it actually followed.
        #
        # **None is a 404 and must never be a fallback to `began_at`.** A stop begins when a
        # part stops following one that left; with no earlier part there is nothing in the
        # database saying the line was ever producing, so a "stop" starting there would be
        # built entirely out of the caller's own string. §6.5 has M4 verify every cited id
        # against the database precisely to catch one the model invented — and an invented
        # instant before the history horizon would have passed that check, come back
        # self-consistent, and been cited as a line stop measured in years.
        from_ts = queries.output_at_or_before(conn, began_at)
        if from_ts is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no stop at {began_at.isoformat()}: no part left S4 at or before it, "
                    "so nothing in the database says the line was producing there"
                ),
            )
        ended_at = queries.output_after(conn, began_at)
        # An open stop is measured to the clock, and `open` is what says the end is a
        # reading of now rather than an observation of a part.
        to_ts = ended_at if ended_at is not None else now
        duration = to_ts - from_ts
        if duration <= settings.micro_stop_threshold:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no stop at {began_at.isoformat()}: the line was without output for "
                    f"{duration.total_seconds():.1f} s there, which is at or under the "
                    f"{settings.micro_stop_threshold_seconds:.0f} s micro-stop threshold"
                ),
            )

        history_from = from_ts - settings.propagation_history
        # One tick past the stop's end, and it has to be: the transitions that *ended* the
        # stop are stamped at exactly that instant, and a half-open read would drop them —
        # leaving every bar in the Gantt open-ended and the recovery invisible.
        history_to = to_ts + _ONE_TICK
        episodes = queries.state_episodes(conn, history_from, history_to)
        levels = queries.buffer_samples(conn, history_from, history_to)
        topology = queries.topology(conn)
        stop_window = windows.Window(from_ts, to_ts)
        alarms = queries.alarms_overlapping(conn, stop_window, None)
        level_points = queries.buffer_level_points(conn, history_from, history_to)
        # The same argument `StopList` makes, and it is stronger here: this endpoint
        # reconstructs *both* boundaries from `part_dispositions` rows that are not there.
        # An ingest gap is an absence that looks exactly the same from here, so a
        # five-minute gateway outage would otherwise resolve to a five-minute line stop
        # with an unexplained derivation -- §4.4's failure, stated as a diagnosis.
        coverage = coverage_of(conn, stop_window)

    derivation = _derivation(episodes, levels, topology, from_ts, to_ts, settings)
    return StopDetail(
        stop=Stop(
            id=stop_id(from_ts),
            from_ts=from_ts,
            to_ts=to_ts,
            duration_seconds=duration.total_seconds(),
            started_before_window=False,
            open_at_window_end=ended_at is None,
            category=derivation.category,
        ),
        as_of=now,
        coverage=coverage,
        history_from_ts=history_from,
        timeline=queries.episode_models(episodes),
        buffer_levels=level_points,
        alarms=alarms,
        derivation=derivation,
    )


def _identifier(
    started_before_window: bool, from_ts: datetime, earlier_output: datetime | None
) -> str | None:
    """The stop's citable id, or None when the database holds nothing to anchor one to.

    A stop that was already running when the window opened begins at the part that left
    before it, not at the window's edge -- the edge is a property of the question and would
    give the same stop a different id in every window it appeared in.

    None when there is no such part: the window opens before the history does, the stop's
    start is the caller's own boundary, and there is no instant to cite that /stops/{id}
    could verify. An id minted here that answered 404 there would be a citation the agent
    could name and nobody could open (§6.5).
    """
    if not started_before_window:
        return stop_id(from_ts)
    return None if earlier_output is None else stop_id(earlier_output)


def _derivation(
    episodes: list[StationEpisode],
    levels: list[BufferSample],
    topology: LineTopology,
    from_ts: datetime,
    to_ts: datetime,
    settings: Settings,
) -> Derivation:
    """§5.4's chain for the stop running `[from_ts, to_ts)`, or the reason there is none.

    The walk starts at the tail of the line because that is where the missing output was
    missed. A topology with no single tail is not a line this can walk, and saying so is the
    honest answer — an arbitrary station picked to have something to return would produce a
    chain that looks like evidence.
    """
    tail = topology.tail()
    if tail is None:
        return _no_derivation(
            "the line records no buffers, so there is no tail station to walk back from"
        )

    walked = derive_chain(
        station=tail,
        at=_seed_instant(episodes, tail, from_ts, to_ts),
        episodes=episodes,
        levels=levels,
        topology=topology,
        lead_in=settings.propagation_lead_in,
    )
    return Derivation(
        links=[_link(link) for link in walked.links],
        termination=walked.termination,
        category=walked.category,
        cause_candidates=queries.episode_models(list(walked.cause_candidates)),
        unexplained=(
            None
            if walked.unexplained is None
            else Unexplained(
                station=walked.unexplained.station,
                buffer=walked.unexplained.buffer,
                detail=walked.unexplained.detail,
            )
        ),
    )


def _no_derivation(detail: str) -> Derivation:
    return Derivation(
        links=[],
        termination=Termination.UNEXPLAINED,
        category=None,
        cause_candidates=[],
        unexplained=Unexplained(station=None, buffer=None, detail=detail),
    )


def _link(link: ChainLink) -> DerivationLink:
    return DerivationLink(
        station=link.station,
        state=link.episode.state,
        from_ts=link.episode.from_ts,
        to_ts=link.episode.to_ts,
        reason=link.episode.reason,
        buffer=link.buffer,
        buffer_condition_since=link.buffer_condition_since,
    )


def _seed_instant(
    episodes: list[StationEpisode], station: str, from_ts: datetime, to_ts: datetime
) -> datetime:
    """Where the walk starts: when the tail station stopped running, inside the stop.

    A stop is bounded by two parts leaving, and at the first of those instants the tail was
    still producing — seeding there lands on `Execute`, which explains nothing, and the
    chain would end before it began. So the seed is the start of the tail's first
    non-`Execute` episode inside the stop.

    Falling back to the stop's own start covers two real shapes. The tail was already in a
    non-`Execute` state when the stop began — the walk then finds the covering episode
    itself — or it never left `Execute` at all, which is a tail that went on running while
    nothing came out of it, and that station is then the thing to be explained rather than a
    consequence of something upstream.
    """
    starts = [
        episode.from_ts
        for episode in episodes
        if episode.station == station
        and episode.state != EXECUTE_STATE
        and from_ts <= episode.from_ts < to_ts
    ]
    return min(starts) if starts else from_ts
