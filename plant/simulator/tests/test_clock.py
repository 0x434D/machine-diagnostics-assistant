from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from simulator.clock import Phase, SimulatedClock, required_history_depth
from simulator.config import ClockConfig

BERLIN = ZoneInfo("Europe/Berlin")


def _clock(depth_h: float, speed: float, wall: list[datetime]) -> SimulatedClock:
    return SimulatedClock(
        ClockConfig(history_depth=timedelta(hours=depth_h), catchup_speed=speed),
        wall_fn=lambda: wall[0],
    )


def test_catchup_duration_matches_the_spec_arithmetic() -> None:
    """§3.2 says 18 h at 600x is about 108 s of wall clock. Simulated time gains
    (speed - 1) seconds per wall second, so the depth closes in depth/(speed-1)."""
    wall = [datetime(2026, 9, 12, 12, 0, tzinfo=UTC)]
    clock = _clock(18, 600, wall)
    assert abs(clock.catchup_duration.total_seconds() - 108.18) < 0.1


def test_phases_run_in_order_and_live_speed_is_exactly_one() -> None:
    boot = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    wall = [boot]
    clock = _clock(18, 600, wall)

    assert clock.phase is Phase.CATCHUP
    assert clock.now() == boot - timedelta(hours=18)

    wall[0] = boot + timedelta(seconds=54)  # halfway
    assert clock.phase is Phase.CATCHUP
    assert clock.now() < wall[0]

    wall[0] = boot + timedelta(seconds=200)  # past catch-up
    # mypy narrowed clock.phase to Literal[Phase.CATCHUP] from the assert above and
    # cannot see that mutating the captured `wall` list changes what the property
    # returns, so it reads this as a check between non-overlapping literals. It isn't:
    # phase is genuinely CATCHUP before and LIVE after, which is exactly what this test
    # verifies, and the run below passes.
    assert clock.phase is Phase.LIVE  # type: ignore[comparison-overlap]
    assert clock.now() == wall[0]  # exactly 1.0, not approximately

    wall[0] = boot + timedelta(seconds=260)
    assert clock.now() == wall[0]


def test_eighteen_hours_does_not_always_contain_a_completed_night_shift() -> None:
    """§3.2: 'The 18 h default exists so the previous night shift lies fully inside
    history at startup.' That holds for a morning boot and fails for an evening one.
    Night is 22-06 Europe/Berlin; the most recent *completed* night shift at 21:59
    started 23 h 59 min earlier."""
    morning = datetime(2026, 9, 12, 9, 0, tzinfo=BERLIN)
    assert required_history_depth(morning) <= timedelta(hours=18)

    evening = datetime(2026, 9, 12, 21, 59, tzinfo=BERLIN)
    assert required_history_depth(evening) > timedelta(hours=18)


def test_required_depth_survives_the_autumn_dst_night() -> None:
    """The night of the autumn transition is nine hours long, which is the worst
    case the default must cover."""
    evening_after_transition = datetime(2026, 10, 25, 21, 59, tzinfo=BERLIN)
    worst = required_history_depth(evening_after_transition)
    assert timedelta(hours=24) < worst <= timedelta(hours=26)


def test_the_default_depth_covers_every_boot_time_in_the_year() -> None:
    """This is the test that produces the number Task 17 writes into §3.2."""
    start = datetime(2026, 1, 1, tzinfo=BERLIN)
    worst = max(
        required_history_depth(start + timedelta(hours=h)) for h in range(366 * 24)
    )
    assert worst <= ClockConfig.DEFAULT_HISTORY_DEPTH
