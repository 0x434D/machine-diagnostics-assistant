"""M2a's authenticity proof (§1): **stop S2, and S3 starves once B2_3 drains.**

§1's standard is a test that would fail if the link were a facade. The facade here
would be a line whose stations stop in sympathy -- four machines wired to one flag --
rather than because a buffer between two of them actually ran empty. What separates
the two is *delay*, and delay is what is measured here: S3 runs exactly as many more
parts as B2_3 was holding, then suspends naming that buffer, and S4 follows one buffer
later.

**Nothing here hardcodes thirty seconds.** §3.1 quotes "roughly 30 s" for a buffer at
its capacity of five and a 6 s takt, and that figure is only about anything because S3
is the bottleneck (6.00 s) and S1 and S2 run faster (5.70, 5.85), so the buffers above
S3 fill. In steady state B2_3 oscillates between 4 and 5 -- measured -- so the delay
this proof observes is 24-32 s depending on which of the two the hold lands on. A test
asserting 30 would be right about half the time, and would be proving the constant
rather than the plant.

These carry the `authenticity` marker, so `make verify` selects them. They are *not*
excluded from `make check`: they start no container and take a few seconds, so keeping
them out of the gate would cost nothing and buy nothing.
"""

from __future__ import annotations

from datetime import datetime
from math import sqrt

import pytest
from conftest import STATION_CODES, build_running_line
from simulator.buffers import Buffer
from simulator.config import Settings
from simulator.line import CARRIER_RETURN, Line
from simulator.packml import State, SuspendReason

pytestmark = pytest.mark.authenticity

S1, S2, S3, S4 = STATION_CODES
"""§4.1's browse names, in line order, read from the tree rather than spelled here.
`Line` keys its state machines on exactly these, so a short "S2" raises KeyError."""

B1_2, B2_3, B3_4 = "B1_2", "B2_3", "B3_4"

_SETTLE_CEILING = 4_000
"""Steps allowed for B1_2 and B2_3 to reach capacity. They do so at ~470 and ~634
steps with the shipped takts; this is a bound on a loop, not an estimate of one. The
spread is what fills them, so a ceiling reached means the spread has been narrowed --
see `_settle`'s failure message."""

_DRAIN_CEILING = 2_000
"""Steps allowed for the whole propagation chain to reach S4 after S2 is held. It
takes ~40 with the shipped settings: a buffer that never drains is a line that is
still being fed from somewhere, which is the defect this proof exists to find."""

_JITTER_SIGMAS = 4.0
"""How much takt jitter the elapsed-time bound tolerates. Four sigma over the sum of
the takts in the window, so a run that fails the bound failed on the mechanism rather
than on a tail draw."""


def _buffer(line: Line, buffer_id: str) -> Buffer:
    """Raises StopIteration for a buffer this line does not carry."""
    return next(buf for buf in line.buffers if buf.buffer_id == buffer_id)


async def _settle(line: Line) -> dict[str, int]:
    """Run until §3.1's two upstream buffers have both been full at least once, and
    return the peak level each buffer reached.

    A step count would be a constant standing in for the thing that actually matters,
    which is that the line has reached the state §3.1 describes. Raises AssertionError
    naming the peaks if it does not get there, because that failure has one cause
    worth reading: the takt spread is too small and S3 is not really the bottleneck.
    """
    peaks = {buf.buffer_id: 0 for buf in line.buffers}
    for _ in range(_SETTLE_CEILING):
        await line.step()
        for buf in line.buffers:
            peaks[buf.buffer_id] = max(peaks[buf.buffer_id], buf.level)
        if (
            peaks[B1_2] >= _buffer(line, B1_2).capacity
            and peaks[B2_3] >= _buffer(line, B2_3).capacity
        ):
            return peaks
    raise AssertionError(
        f"B1_2 and B2_3 did not both fill in {_SETTLE_CEILING} steps (peaks {peaks}). "
        "§3.1 fills them by running S1 and S2 faster than S3; if they no longer fill, "
        "the takt spread is too small and S3 is not the bottleneck the whole "
        "propagation claim rests on"
    )


async def _hold_s2_in_execute(
    line: Line, reason: str
) -> tuple[datetime, dict[str, int]]:
    """Step until S2 and S3 are both in `Execute`, then hold S2 at the next due
    instant. Returns that instant.

    `Line.hold` raises unless the station is in `Execute`, which PackML requires and
    which S2 leaves whenever B2_3 is full -- 19 of 755 cycles measured in steady
    state, so holding at an arbitrary moment is a one-in-forty flake. S3 is waited for
    too: a proof that begins with S3 already suspended has nothing left to observe.

    Returns every buffer's level **at that instant**, because the wait above is what
    makes a level read before the call stale -- and it goes stale exactly when the wait
    engages rather than never. Measured over 400 warm-up offsets: at the shipped
    settings the wait moved B2_3 on 6 of them, and at a retune to S3 = 8.0 s on 73. A
    caller reading the level for itself beforehand therefore passes on every shipped
    run and is wrong the first time anyone retunes, which is worse than a flake:
    nothing ever goes red to say so.
    """
    for _ in range(_DRAIN_CEILING):
        if all(line.machine_for(code).state is State.EXECUTE for code in (S2, S3)):
            at = line.next_due
            assert at is not None, (
                "every station reschedules itself, so the queue is never dry"
            )
            await line.hold(S2, at, reason)
            return at, {buf.buffer_id: buf.level for buf in line.buffers}
        await line.step()
    raise AssertionError(
        f"S2 and S3 were never both in Execute at once: {line.station_states}"
    )


@pytest.mark.asyncio
async def test_s3_starves_exactly_when_b2_3_drains_and_not_before() -> None:
    """The proof. S3 runs exactly as many more parts as B2_3 was holding when S2
    stopped -- no fewer, which would be sympathy, and no more, which would be a part
    arriving from somewhere the model does not have.

    The expected delay is derived from the level observed **at** the moment of the
    hold -- `_hold_s2_in_execute` returns it, because it may step the line to reach a
    moment S2 is in `Execute` -- so this survives a retune of the takts or of
    `buffer_capacity`; §3.1's figure is checked as the interval that level implies, not
    as the number 30.
    """
    settings = Settings()
    line, _clock, _nodes = await build_running_line(settings)
    await _settle(line)

    held_at, levels_at_stop = await _hold_s2_in_execute(line, "propagation proof")
    level_at_stop = levels_at_stop[B2_3]
    starved_on_b2_3 = str(SuspendReason("starved", B2_3))

    produced_by_s3 = 0
    starved_at: datetime | None = None
    for _ in range(_DRAIN_CEILING):
        outcome = await line.step()
        assert outcome is not None
        if outcome.station_code == S3 and outcome.produced:
            produced_by_s3 += 1
        machine = line.machine_for(S3)
        if machine.state is State.SUSPENDED and machine.reason == starved_on_b2_3:
            starved_at = outcome.at
            break

    assert starved_at is not None, (
        f"S3 never starved on {B2_3} in {_DRAIN_CEILING} steps with S2 held; it "
        f"produced {produced_by_s3} parts from a buffer holding {level_at_stop}"
    )
    # The claim, exactly and with no tolerance: the buffer is the only thing between a
    # stopped S2 and a starved S3, so what it was holding is what S3 got.
    assert produced_by_s3 == level_at_stop

    # And the same claim as an interval, which is what §3.1 quotes. S3's suspending
    # cycle is its (level + 1)th after the hold, and the hold lands somewhere inside
    # the takt S3 is already in -- so the delay is between `level` and `level + 1`
    # takts, widened by the jitter on that many draws.
    takt = settings.station_takt_seconds[S3]
    slack = _JITTER_SIGMAS * settings.takt_jitter_sigma * sqrt(level_at_stop + 1)
    delay = (starved_at - held_at).total_seconds()
    assert (
        level_at_stop * takt - slack <= delay <= (level_at_stop + 1) * takt + slack
    ), (
        f"B2_3 held {level_at_stop} at {takt} s per part, so S3 should starve "
        f"{level_at_stop * takt:.0f}-{(level_at_stop + 1) * takt:.0f} s later; "
        f"measured {delay:.2f} s"
    )


@pytest.mark.asyncio
async def test_s4_starves_after_s3_and_not_at_the_same_time() -> None:
    """Propagation is a sequence, not an event. If both suspend on the same cycle the
    buffers are decoupling nothing and the whole model is decorative.

    Measured against the *final* suspension of each station rather than the first.
    S4's first suspension after a hold can be an ordinary starvation episode that
    clears again -- `Suspended` is a consequence and does clear itself -- and comparing
    first occurrences would be comparing one station's permanent stop against another's
    passing one.
    """
    line, _clock, _nodes = await build_running_line(Settings())
    await _settle(line)
    await _hold_s2_in_execute(line, "propagation proof")

    # The instant each station last *entered* Suspended: overwritten whenever it comes
    # back out, so what survives the run is the start of the episode it never left.
    entered: dict[str, datetime] = {}
    was_suspended = {S3: False, S4: False}
    for _ in range(_DRAIN_CEILING):
        outcome = await line.step()
        assert outcome is not None
        for code in (S3, S4):
            suspended = line.machine_for(code).state is State.SUSPENDED
            if suspended and not was_suspended[code]:
                entered[code] = outcome.at
            was_suspended[code] = suspended
        if all(was_suspended.values()) and set(entered) == {S3, S4}:
            break

    assert all(was_suspended.values()), (
        f"S3 and S4 did not both end up suspended with S2 held: {line.station_states}"
    )
    assert entered[S3] < entered[S4], (
        "S4 starved no later than S3, so B3_4 decoupled nothing: "
        f"S3 at {entered[S3].isoformat()}, S4 at {entered[S4].isoformat()}"
    )
    assert line.machine_for(S4).reason == str(SuspendReason("starved", B3_4))


@pytest.mark.asyncio
async def test_s2_itself_is_held_rather_than_suspended() -> None:
    """§3.3's cause/consequence split, which is the thing M3 will reason over: the
    station that *is* the problem must not look like the ones merely waiting.

    This is also what the plant HMI colours on -- red for a station that needs an
    operator, amber for one waiting on someone else -- so a line that lost the
    distinction would still draw, in one colour, and read as four equal faults.
    """
    line, _clock, _nodes = await build_running_line(Settings())
    await _settle(line)
    await _hold_s2_in_execute(line, "propagation proof")
    for _ in range(_DRAIN_CEILING):
        await line.step()

    s2, s3 = line.machine_for(S2), line.machine_for(S3)
    assert s2.state is State.HELD
    assert s2.reason == "propagation proof"
    assert s2.clears_itself is False
    assert s3.state is State.SUSPENDED
    assert s3.reason == str(SuspendReason("starved", B2_3))
    assert s3.clears_itself is True


@pytest.mark.asyncio
async def test_the_line_settles_where_3_1_says_it_does() -> None:
    """Three things §3.1 and Task 3's configuration assume, confirmed against the real
    four stations rather than against the test doubles they were tuned on.

    1. B1_2 and B2_3 reach capacity and B3_4 does not. That is what makes S3 the
       bottleneck and what gives the proof above a buffer level to derive from.
    2. S1 never suspends for want of a free carrier in free running. `carrier_count`
       was raised 12 -> 18 on a measurement made with `FakeStation`; this is the
       confirmation against the stations that actually hold carriers.
    3. S3 is not starved across this window, and waits less often than the two stations
       above it. The ordering is the structural claim -- the further up the line a
       station sits, the more it waits -- and it is what stops being true first if the
       takts move.

    **§3.5's noise floor makes two of these weaker than they were, and the numbers are
    measured rather than reasoned.** A micro-stop is a station running slow for up to
    25 s, so it can briefly empty a buffer or fill one, and both now happen: over
    400,000 steady-state steps at the shipped takts S3 takes `blocked:B3_4` 28 times and
    `starved:B2_3` 12 times, against ~98,000 cycles. Neither falls in this test's own
    window, which is seeded and therefore the same run every time; what the window
    cannot show is that "never" has become "0.03 % of cycles, and only behind a
    micro-stop above it". That is the noise §3.5 asks for -- something that sometimes
    looks like a cause and is not.
    """
    settings = Settings()
    line, _clock, _nodes = await build_running_line(settings)
    peaks = await _settle(line)

    census: dict[tuple[str, str], int] = {}
    cycles: dict[str, int] = dict.fromkeys(STATION_CODES, 0)
    for _ in range(_SETTLE_CEILING):
        outcome = await line.step()
        assert outcome is not None
        cycles[outcome.station_code] += 1
        for buf in line.buffers:
            peaks[buf.buffer_id] = max(peaks[buf.buffer_id], buf.level)
        if outcome.state is State.SUSPENDED:
            key = (outcome.station_code, outcome.reason)
            census[key] = census.get(key, 0) + 1

    capacity = settings.buffer_capacity
    assert peaks[B1_2] == capacity, f"B1_2 never filled: peaked at {peaks[B1_2]}"
    assert peaks[B2_3] == capacity, f"B2_3 never filled: peaked at {peaks[B2_3]}"
    assert peaks[B3_4] < capacity, (
        f"B3_4 reached {peaks[B3_4]} of {capacity}. It sits near empty because S4 runs "
        "slightly faster than S3 and therefore drains it; a B3_4 that stays full means "
        "that restoring force is gone and blockage, not starvation, is what propagates "
        "from here"
    )

    carrier_starvation = (S1, str(SuspendReason("starved", CARRIER_RETURN)))
    assert carrier_starvation not in census, (
        f"S1 suspended on {CARRIER_RETURN} {census[carrier_starvation]} times in free "
        f"running with carrier_count={settings.carrier_count}. That is the plant "
        "inventing an upstream supply fault nobody injected, and §3.1's figure needs "
        "the raised count recorded"
    )

    # Nothing in the configured takts can starve the slowest station: S2 outruns it, so
    # B2_3 cannot empty. A starved S3 means either the takts no longer make it the
    # slowest, or a micro-stop above it lasted longer than B2_3 took to drain.
    starved_s3 = {
        reason: count
        for (code, reason), count in census.items()
        if code == S3 and reason.startswith("starved")
    }
    assert not starved_s3, (
        f"S3 was starved in free running: {starved_s3}. The slowest station runs out of "
        "parts only when something above it was momentarily slower still -- which a "
        "micro-stop at S2 is, 12 times in 400,000 steps and none of them here. More "
        "than that in this window means the takts, not the noise floor, put it there"
    )

    # And it waits least. A threshold would be a number standing in for the claim; the
    # ordering IS the claim, and it is what stops being true first if the takts move.
    waits = {
        code: sum(count for (owner, _), count in census.items() if owner == code)
        for code in (S1, S2, S3)
    }
    assert waits[S3] < waits[S2] < waits[S1], (
        f"the further up the line a station sits, the more it should wait: {waits}. "
        f"Cycle counts were {cycles}"
    )
    assert cycles[S3] < cycles[S2] < cycles[S1]
