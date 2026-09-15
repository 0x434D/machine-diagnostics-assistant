"""§5.4's stop definition, exercised through output alone.

Every fixture here is a list of instants at which a part left S4, and nothing else.
That is not a convenience: a stop *is* the absence of output, and a test that also
handed the detector a state timeline could not tell an implementation that read the
definition from one that read the intuition.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from analysis.stops import DEFAULT_MICRO_STOP_THRESHOLD, detect_stops
from analysis.windows import Window

WINDOW_START = datetime(2026, 9, 12, 2, 0, 0, tzinfo=UTC)

TAKT = timedelta(seconds=6)
"""§3.1's line takt. The fixtures run at it because §5.4 states its threshold in
missed cycles of it."""


def _run(first: datetime, count: int) -> list[datetime]:
    return [first + TAKT * index for index in range(count)]


def _window_around(parts: list[datetime]) -> Window:
    """A window whose two edges are one ordinary cycle away from the outermost part,
    so that neither edge is itself an interruption. Every test that is not about a
    window edge uses this, so a gap in the result came from the fixture."""
    return Window(parts[0] - TAKT, parts[-1] + TAKT)


def test_a_clean_run_produces_no_stops_and_no_micro_stops() -> None:
    parts = _run(WINDOW_START + TAKT, 600)

    detection = detect_stops(parts, _window_around(parts))

    assert detection.stops == ()
    assert detection.micro_stops == 0


def test_a_forty_five_second_interruption_is_a_micro_stop_not_a_stop() -> None:
    before = _run(WINDOW_START + TAKT, 10)
    after = _run(before[-1] + timedelta(seconds=45), 10)
    parts = before + after

    detection = detect_stops(parts, _window_around(parts))

    assert detection.stops == ()
    assert detection.micro_stops == 1


def test_a_ninety_second_interruption_is_one_stop_with_its_boundaries() -> None:
    before = _run(WINDOW_START + TAKT, 10)
    after = _run(before[-1] + timedelta(seconds=90), 10)
    parts = before + after

    detection = detect_stops(parts, _window_around(parts))

    assert len(detection.stops) == 1
    stop = detection.stops[0]
    assert stop.from_ts == before[-1]
    assert stop.to_ts == after[0]
    assert stop.duration_seconds == 90.0
    assert stop.open_at_window_end is False
    assert stop.started_before_window is False
    # A gap that is a stop is not also counted among the interruptions below it.
    assert detection.micro_stops == 0


def test_two_interruptions_thirty_seconds_apart_are_two_events() -> None:
    """Thirty seconds of production between two stoppages is five parts out of S4.
    A detector that merged them would report one stop of 210 s that never happened."""
    first_run = _run(WINDOW_START + TAKT, 10)
    second_run = _run(first_run[-1] + timedelta(seconds=90), 6)
    assert second_run[-1] - second_run[0] == timedelta(seconds=30)
    third_run = _run(second_run[-1] + timedelta(seconds=90), 10)
    parts = first_run + second_run + third_run

    detection = detect_stops(parts, _window_around(parts))

    assert len(detection.stops) == 2
    assert detection.stops[0].from_ts == first_run[-1]
    assert detection.stops[0].to_ts == second_run[0]
    assert detection.stops[1].from_ts == second_run[-1]
    assert detection.stops[1].to_ts == third_run[0]


def test_a_stop_still_running_at_the_window_end_is_reported_as_open() -> None:
    parts = _run(WINDOW_START + TAKT, 10)
    window = Window(parts[0] - TAKT, parts[-1] + timedelta(seconds=90))

    detection = detect_stops(parts, window)

    assert len(detection.stops) == 1
    stop = detection.stops[0]
    assert stop.from_ts == parts[-1]
    assert stop.to_ts == window.to_ts
    assert stop.open_at_window_end is True
    assert stop.duration_seconds == 90.0


def test_a_part_on_the_exclusive_edge_does_not_close_an_open_stop() -> None:
    """The window is half-open, so a part at `to_ts` belongs to the next one. Reading
    it as the part that ended this stop would report a closed stop whose closing part
    this window never saw."""
    parts = _run(WINDOW_START + TAKT, 10)
    window = Window(parts[0] - TAKT, parts[-1] + timedelta(seconds=90))

    detection = detect_stops([*parts, window.to_ts], window)

    assert len(detection.stops) == 1
    assert detection.stops[0].open_at_window_end is True


def test_an_open_stop_and_one_closed_at_the_edge_are_different_facts() -> None:
    parts = _run(WINDOW_START + TAKT, 10)
    window = Window(parts[0] - TAKT, parts[-1] + timedelta(seconds=90))
    closing_part = window.to_ts - timedelta(milliseconds=1)

    still_open = detect_stops(parts, window).stops[0]
    closed = detect_stops([*parts, closing_part], window).stops[0]

    assert still_open.open_at_window_end is True
    assert closed.open_at_window_end is False
    assert closed.to_ts == closing_part
    assert still_open != closed


def test_a_stop_already_running_at_the_window_start_is_reported_as_truncated() -> None:
    """The mirror of the open stop: the window's first part is 90 s in, so the line
    was already down when the window opened and the stop began before anything this
    window can see."""
    parts = _run(WINDOW_START + timedelta(seconds=90), 10)
    window = Window(WINDOW_START, parts[-1] + TAKT)

    detection = detect_stops(parts, window)

    assert len(detection.stops) == 1
    stop = detection.stops[0]
    assert stop.from_ts == WINDOW_START
    assert stop.to_ts == parts[0]
    assert stop.started_before_window is True


def test_the_threshold_is_a_parameter() -> None:
    """§10.3: every number is configuration. The same 45 s interruption is a
    micro-stop under §5.4's 60 s and a stop on a line whose threshold is 30 s."""
    before = _run(WINDOW_START + TAKT, 10)
    after = _run(before[-1] + timedelta(seconds=45), 10)
    parts = before + after
    window = _window_around(parts)

    assert DEFAULT_MICRO_STOP_THRESHOLD == timedelta(seconds=60)
    assert detect_stops(parts, window).stops == ()

    tighter = detect_stops(parts, window, micro_stop_threshold=timedelta(seconds=30))

    assert len(tighter.stops) == 1
    assert tighter.micro_stops == 0


def test_output_drawn_from_a_buffer_after_a_station_aborts_is_not_a_stop() -> None:
    """**The test that proves the definition was implemented rather than the intuition.**

    S2 aborts and never runs again. S4 keeps emitting for another five carriers — the
    parts already in B3_4 — and only then does output cease. §5.4 says a stop begins
    when output ceases, so this run has exactly one stop and it begins 30 s after the
    abort rather than at it.

    An implementation that read `state_changes` instead would pass every other test in
    this file and put the stop's start at `aborted_at`.
    """
    running = _run(WINDOW_START + TAKT, 100)
    aborted_at = running[-1]
    drained = _run(aborted_at + TAKT, 5)
    assert drained[-1] - aborted_at == timedelta(seconds=30)
    parts = running + drained
    window = Window(parts[0] - TAKT, drained[-1] + timedelta(seconds=90))

    detection = detect_stops(parts, window)

    assert len(detection.stops) == 1
    stop = detection.stops[0]
    assert stop.from_ts == drained[-1]
    assert stop.from_ts != aborted_at
    assert stop.duration_seconds == 90.0
