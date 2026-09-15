"""§4.2's alarms, asserted by what appears on the line and never by what was injected.

The hazard this milestone keeps hitting, one layer down: a test that asserts an alarm
*was raised* proves the raise call was made. Each test below reads the plant's own output
-- the published `JoiningForcePeak`, the published `State`, the events that reached the
station's stream -- so an alarm that fired and shut nothing down, or shut a station down
and published nothing, fails here.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import RecordingNodes, build_running_line, new_clock
from simulator.alarms import (
    JOINING_FORCE_OUT_OF_TOLERANCE,
    AlarmSystem,
    ForceTolerance,
)
from simulator.clock import SimulatedClock
from simulator.config import Settings
from simulator.events import ALARM
from simulator.faults import NO_FAULTS, FaultKind, FaultSet
from simulator.ground_truth import INJECTION, OPERATOR, Injector, open_log
from simulator.line import Line
from simulator.packml import State
from simulator.scenarios import scenario

T0 = datetime(2026, 9, 13, 6, 0, tzinfo=UTC)


async def _run(
    seconds: float,
    *,
    faults: FaultSet = NO_FAULTS,
    settings: Settings | None = None,
    clock: SimulatedClock | None = None,
) -> tuple[Line, SimulatedClock, dict[str, RecordingNodes]]:
    """A real four-station line, stepped for `seconds` of simulated time."""
    settings = settings or Settings()
    clock = clock or new_clock(settings)
    line, clock, nodes = await build_running_line(settings, faults=faults, clock=clock)
    horizon = clock.history_start + timedelta(seconds=seconds)
    while (due := line.next_due) is not None and due < horizon:
        await line.step()
    return line, clock, nodes


def _states(nodes: RecordingNodes) -> list[tuple[datetime, str]]:
    """Every `State` this station published, in the order it published them."""
    return [(at, str(value)) for signal, at, value in nodes.writes if signal == "State"]


async def _drifted(settings: Settings, seconds: float) -> tuple[Line, RecordingNodes]:
    """A line carrying §3.5's scenario 3, run until `seconds` of simulated time."""
    clock = new_clock(settings)
    line, _clock, nodes = await _run(
        seconds,
        faults=scenario(3, settings).fault_set(clock.history_start),
        settings=settings,
        clock=clock,
    )
    return line, nodes["S2_Joining"]


# --- the condition -------------------------------------------------------------------


def test_the_band_latches_on_a_run_of_parts_and_not_on_one() -> None:
    """The debounce, at the level it is decided.

    One part outside the band is a draw: without the run, the tail of a Gaussian shuts a
    station down for an hour with no process behind it. The counter resets on any part
    back inside the band, so it is a *consecutive* run and not a tally -- a tally would
    eventually latch on any line at all, given enough parts.
    """
    settings = Settings()
    half = settings.joining_force_tolerance_newtons
    inside = settings.joining_force_nominal
    outside = settings.joining_force_nominal - half - 1.0

    tolerance = ForceTolerance(settings)
    assert not any(tolerance.observe(inside) for _ in range(100))

    # One short of the run, then back inside: the count starts again from nothing.
    for _ in range(settings.alarm_consecutive_parts - 1):
        assert not tolerance.observe(outside)
    assert not tolerance.observe(inside)
    for _ in range(settings.alarm_consecutive_parts - 1):
        assert not tolerance.observe(outside)
    assert tolerance.observe(outside)

    # And the latch resets, so the station an operator brings back up has to earn its
    # next alarm rather than tripping on its first stroke.
    assert not tolerance.observe(outside)


def test_the_band_is_symmetric_and_catches_a_press_clamping_too_hard() -> None:
    """§4.2's alarm is "out of tolerance", not "below tolerance". Nothing in §3.5 drifts
    the clamp upwards today, and a limit that only existed on one side would be an alarm
    written for one scenario rather than for the press."""
    settings = Settings()
    high = (
        settings.joining_force_nominal + settings.joining_force_tolerance_newtons + 1.0
    )
    tolerance = ForceTolerance(settings)
    assert [tolerance.observe(high) for _ in range(settings.alarm_consecutive_parts)][
        -1
    ]


# --- the clean line ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_line_never_raises_an_alarm() -> None:
    """The half that would fail silently, and the reason the band is stated in sigmas.

    A tolerance one sigma too tight puts A-207 and an aborted S2 into the history of a
    line with nothing wrong with it, and every later milestone would then be looking for
    §3.5 row 3 in a plant that produces it on a clean run. Measured over four simulated
    hours: the published peak is 8.4 sigma inside the nearer edge and the alarm needs
    three consecutive parts outside it.

    The part-count assertion is not decoration: over an empty run "no alarms" is true of
    a line that never pressed anything.
    """
    settings = Settings()
    line, _clock, nodes = await _run(4.0 * 3600, settings=settings)

    peaks = [
        float(value)
        for signal, _at, value in nodes["S2_Joining"].writes
        if signal == "JoiningForcePeak"
    ]
    assert len(peaks) > 2000, (
        f"S2 pressed {len(peaks)} parts in four simulated hours, which is too few for "
        "this to say anything about a clean line"
    )
    assert line.alarms.alarms == (), (
        f"a clean line raised {len(line.alarms.alarms)} alarm(s); the peak ran "
        f"{min(peaks):.1f}-{max(peaks):.1f} N against a band of "
        f"+/-{settings.joining_force_tolerance_newtons} N around "
        f"{settings.joining_force_nominal} N"
    )
    # The margin, recorded rather than assumed: this is what says the band is outside the
    # noise rather than merely untouched by this seed.
    spread = statistics.stdev(peaks)
    edge = settings.joining_force_nominal - settings.joining_force_tolerance_newtons
    assert (statistics.fmean(peaks) - edge) / spread > 5.0
    assert all(
        state != State.ABORTED.value for _at, state in _states(nodes["S2_Joining"])
    )


# --- the drift -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_alarm_shuts_the_station_down_after_the_part_that_raised_it() -> None:
    """§3.3: `Aborted` is a fault shutdown. The claim is the sequence, read off what the
    plant published: the peak that left the band, then the alarm, then `Aborting` and
    `Aborted` -- in that order, and with the shutdown on a later `SourceTimestamp` than
    the alarm, because §5.2 keys `state_changes` on `(station_id, source_ts)` and two
    states published at one instant are one row.
    """
    settings = Settings()
    line, s2 = await _drifted(settings, 6000.0)

    assert line.alarms.alarms, "the drift never took the press out of tolerance"
    first = line.alarms.alarms[0]

    published = _states(s2)
    aborting = [at for at, state in published if state == State.ABORTING.value]
    aborted = [at for at, state in published if state == State.ABORTED.value]
    assert aborting and aborted, "S2 never published a shutdown"
    assert first.raised_at <= aborting[0] < aborted[0], (
        f"alarm at {first.raised_at}, Aborting at {aborting[0]}, Aborted at {aborted[0]}"
    )

    # The peak that raised it really was outside the band -- the alarm is a measurement,
    # so the number it was raised on has to be on the wire.
    at_raise = [
        float(value)
        for signal, at, value in s2.writes
        if signal == "JoiningForcePeak" and at == first.raised_at
    ]
    assert at_raise, "nothing was published at the instant the alarm names"
    assert (
        abs(at_raise[0] - settings.joining_force_nominal)
        > settings.joining_force_tolerance_newtons
    )


@pytest.mark.asyncio
async def test_the_operator_acknowledges_and_the_station_produces_again() -> None:
    """§3.5's noise floor: alarms acknowledged after realistic delays, manual restarts.

    A shutdown nobody attended to would make §3.5 row 3 a line that stops once and never
    runs again, which is neither what a shift looks like nor something the row's other
    consequences could be read out of.

    Both halves. The alarm is acknowledged and then cleared, in that order -- a `Held`
    or `Aborted` station needs an operator (§3.3) and clearing it without one would be a
    fault that repaired itself -- and the press is pressing again afterwards.
    """
    settings = Settings()
    line, s2 = await _drifted(settings, 9000.0)

    worked = [alarm for alarm in line.alarms.alarms if alarm.cleared_at is not None]
    assert worked, "no alarm was ever worked; the operator never reached the panel"
    alarm = worked[0]
    acked, cleared = alarm.acked_at, alarm.cleared_at
    assert acked is not None and cleared is not None
    assert alarm.raised_at < acked <= cleared
    delay = (acked - alarm.raised_at).total_seconds()
    assert (
        settings.operator_ack_min_seconds <= delay <= settings.operator_ack_max_seconds
    )

    pressed_after = [
        at
        for signal, at, _value in s2.writes
        if signal == "JoiningForcePeak" and at > cleared
    ]
    assert pressed_after, "S2 never pressed another part after it was restarted"


@pytest.mark.asyncio
async def test_a_station_already_reporting_a_condition_does_not_stack_alarms() -> None:
    """A press nothing repaired is one condition, however many parts read out of
    tolerance. Without this, §3.7's screen would grow an unbounded list of open A-207s
    and each would put its own operator on the way."""
    settings = Settings()
    line, _s2 = await _drifted(settings, 9000.0)

    assert len(line.alarms.alarms) > 1, (
        "the drift raised at most one alarm, so this proves nothing about stacking"
    )
    assert len(line.alarms.active) <= 1
    # Every alarm but (possibly) the last is closed before the next one opens.
    for earlier, later in zip(line.alarms.alarms, line.alarms.alarms[1:], strict=False):
        assert earlier.cleared_at is not None
        assert earlier.cleared_at <= later.raised_at


@pytest.mark.asyncio
async def test_every_lifecycle_transition_reaches_the_stations_event_stream() -> None:
    """§4.2 makes an alarm an event with a lifecycle, and §5.2's row is assembled from
    all three. An alarm the plant knows about and never published is one the diagnostics
    stack can never be asked about, which is the whole boundary this project is built on.

    `AlarmRaisedAt` is what ties the three together, so it is asserted as identical
    across them: it is the alarm's identity, and the gateway keys its row on it.
    """
    settings = Settings()
    line, s2 = await _drifted(settings, 9000.0)
    alarm = next(a for a in line.alarms.alarms if a.cleared_at is not None)

    payloads = [
        (at, fields)
        for name, at, fields in s2.events
        if name == ALARM.name and fields["AlarmRaisedAt"] == alarm.raised_at
    ]
    assert len(payloads) == 3, (
        f"alarm {alarm.sequence} published {len(payloads)} of its three transitions"
    )

    (raise_at, raised), (ack_at, acked), (clear_at, cleared) = payloads
    assert (raise_at, ack_at, clear_at) == (
        alarm.raised_at,
        alarm.acked_at,
        alarm.cleared_at,
    )
    assert (raised["AlarmActive"], raised["AlarmAcknowledged"]) == (True, False)
    assert (acked["AlarmActive"], acked["AlarmAcknowledged"]) == (True, True)
    assert cleared["AlarmActive"] is False
    for fields in (raised, acked, cleared):
        assert fields["AlarmCode"] == JOINING_FORCE_OUT_OF_TOLERANCE.code
        assert fields["AlarmText"] == JOINING_FORCE_OUT_OF_TOLERANCE.text
        assert fields["AlarmSeverity"] == JOINING_FORCE_OUT_OF_TOLERANCE.severity


# --- §3.5's occasional unnecessary reset ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [0.0, 1.0])
async def test_an_unnecessary_reset_leaves_the_line_exactly_where_it_found_it(
    rate: float,
) -> None:
    """§3.5's fifth noise source, and the one that is easiest to ship inert.

    Driven at both ends of its own rate rather than at the shipped 0.08, because one in
    twelve over a handful of interventions is a coin toss: at 0.0 the operator's visit
    leaves no `Stopped` behind, at 1.0 every visit does, and the difference between the
    two is the whole of what `noise.NoiseFloor.is_unnecessary_reset` decides.

    "Changed nothing" is asserted as well as claimed: the station is producing on both
    runs, so the six extra rows in `state_changes` are noise an analysis has to dismiss
    rather than a stoppage it has to explain.
    """
    settings = Settings(operator_unnecessary_reset_rate=rate)
    line, s2 = await _drifted(settings, 9000.0)

    assert line.alarms.alarms, "the drift never took the press out of tolerance"
    # After the first alarm, because a station's own bring-up passes through `Stopped`
    # too -- counting from the start would put the line's start in the operator's total.
    since = line.alarms.alarms[0].raised_at
    stopped = [
        at for at, state in _states(s2) if state == State.STOPPED.value and at > since
    ]
    restarts = sum(1 for alarm in line.alarms.alarms if alarm.cleared_at is not None)
    assert restarts > 1, "one intervention is too few to tell the two rates apart"

    # Every restart passes through Stopped on its way out of Aborted (§3.3's Clearing),
    # so an unnecessary reset is one *extra* Stopped per intervention.
    assert len(stopped) == restarts * (2 if rate == 1.0 else 1)
    assert any(
        signal == "JoiningForcePeak" and at > stopped[-1]
        for signal, at, _value in s2.writes
    ), "S2 stopped producing after the last reset, so it changed something"


# --- §3.7's acknowledge button, and §3.5's rule about the panel ----------------------


@pytest.mark.asyncio
async def test_acknowledging_at_the_panel_brings_the_operator_forward() -> None:
    """§3.7's button, asserted by what it does to the line rather than by what it sets.

    The operator delay is turned up so that the claim has something to be measured
    against: a drawn visit cannot arrive sooner than `operator_ack_min_seconds`, so an
    alarm cleared well inside that was cleared because the button was pressed. At the
    shipped 15 s minimum the two would be a few takts apart and the assertion would pass
    on a line where the button did nothing at all.

    What is asserted is the whole intervention, not the acknowledgement: pressing the
    button takes the same path the drawn delay does, so the station is restarted and
    producing again. A button that only stamped `acked_at` would leave S2 shut down with
    its alarm marked handled, which is the worst of both.
    """
    settings = Settings(
        operator_ack_min_seconds=600.0,
        operator_ack_mode_seconds=900.0,
        operator_ack_max_seconds=1200.0,
    )
    clock = new_clock(settings)
    line, clock, nodes = await build_running_line(
        settings,
        faults=scenario(3, settings).fault_set(clock.history_start),
        clock=clock,
    )
    horizon = clock.history_start + timedelta(seconds=9000)
    pressed = False
    while (due := line.next_due) is not None and due < horizon:
        await line.step()
        if not pressed and line.alarms.alarms:
            alarm = line.alarms.alarms[0]
            line.alarms.acknowledge(alarm.sequence, alarm.raised_at)
            pressed = True

    assert pressed, "the drift never raised an alarm, so the button was never pressed"
    alarm = line.alarms.alarms[0]
    assert alarm.acked_at == alarm.raised_at
    cleared = alarm.cleared_at
    assert cleared is not None
    worked = (cleared - alarm.raised_at).total_seconds()
    assert worked < settings.operator_ack_min_seconds, (
        f"the alarm was cleared {worked:.0f} s after it was raised, which a drawn visit "
        f"could have done on its own (minimum {settings.operator_ack_min_seconds} s)"
    )

    s2 = nodes["S2_Joining"]
    assert any(
        signal == "JoiningForcePeak" and at > cleared
        for signal, at, _value in s2.writes
    ), "S2 was acknowledged and never restarted"


@pytest.mark.asyncio
async def test_a_panel_injection_reaches_the_line_and_the_log_together(
    tmp_path: Path,
) -> None:
    """§3.5's "**every** injection writes to the ground-truth log", followed all the way
    to a number S2 published.

    `test_hmi` asserts the HTTP half -- that the endpoint records what it injects and
    refuses what it cannot. This is the other half and the one that could be quietly
    false: `FaultSet.inject` appends to the object the four stations were built with, and
    a version that rebuilt the set instead would record the injection perfectly and reach
    no station at all. The press is what says which happened.
    """
    settings = Settings()
    clock = new_clock(settings)
    faults = FaultSet((), clock.history_start)
    line, clock, nodes = await build_running_line(settings, faults=faults, clock=clock)

    warmup = clock.history_start + timedelta(seconds=600)
    while (due := line.next_due) is not None and due < warmup:
        await line.step()

    with open_log(tmp_path / "gt.jsonl", settings, clock, None) as log:
        injector = Injector(faults, log, clock.history_start)
        # A step drift rather than a ramp, so the two windows either side of it are
        # separated by the whole magnitude rather than by part of it.
        injector.inject(FaultKind.JOINING_FORCE_DRIFT, {"newtons": -400.0}, warmup)

        horizon = warmup + timedelta(seconds=600)
        while (due := line.next_due) is not None and due < horizon:
            await line.step()

    peaks = [
        ((at - warmup).total_seconds(), float(value))
        for signal, at, value in nodes["S2_Joining"].writes
        if signal == "JoiningForcePeak"
    ]
    before = [value for elapsed, value in peaks if elapsed < 0]
    after = [value for elapsed, value in peaks if elapsed > 0]
    assert before and after
    fall = statistics.fmean(before) - statistics.fmean(after)
    assert fall > 300.0, (
        f"the peak fell {fall:.1f} N on a 400 N injected drift: the panel's fault is not "
        "reaching the press the line is running"
    )

    records = [
        json.loads(line_) for line_ in (tmp_path / "gt.jsonl").read_text().splitlines()
    ]
    injections = [record for record in records if record["record"] == INJECTION]
    assert len(injections) == 1
    assert injections[0]["source"] == OPERATOR
    assert injections[0]["kind"] == str(FaultKind.JOINING_FORCE_DRIFT)
    assert injections[0]["at"] == warmup.isoformat()
    assert injections[0]["until"] is None


def test_an_alarm_on_a_station_with_no_nodes_fails_loudly() -> None:
    """The wiring failure that would otherwise be silent: an alarm system built with the
    wrong station handles raises alarms nobody can see.

    §4.1's tree declares the alarm type on every station, and `server.build_line` hands
    the system a handle for each. A mapping that missed one would leave that station able
    to raise an alarm the history never receives -- so the publish raises rather than
    skipping, and this is the case that says so.
    """
    settings = Settings()
    alarms = AlarmSystem(settings, settings.seed, {})
    outside = settings.joining_force_nominal + settings.joining_force_tolerance_newtons
    for _ in range(settings.alarm_consecutive_parts):
        alarms.observe_joining_force("S2_Joining", T0, outside + 1.0)

    assert len(alarms.alarms) == 1
    with pytest.raises(KeyError, match="S2_Joining"):
        # The publish is the first thing `settle` does, so the line is never reached and
        # nothing here has to be a Line.
        import asyncio

        asyncio.run(alarms.settle(None, T0))  # type: ignore[arg-type]
