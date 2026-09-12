from datetime import UTC, date, datetime, time, timedelta
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
    """Arithmetic check, independent of the configured defaults (which have moved
    on since -- see config.py): simulated time gains (speed - 1) seconds per wall
    second, so the depth closes in depth/(speed-1). 18 h at 600x is 108.18 s wall,
    whatever the real defaults are."""
    wall = [datetime(2026, 9, 12, 12, 0, tzinfo=UTC)]
    clock = _clock(18, 600, wall)
    assert abs(clock.catchup_duration.total_seconds() - 108.18) < 0.1


def test_phases_run_in_order_and_live_speed_is_exactly_one() -> None:
    boot = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
    wall = [boot]
    clock = _clock(18, 600, wall)

    # Collected rather than asserted inline: mypy narrows a repeated `clock.phase is
    # X` to a literal type and cannot see that mutating the captured `wall` list
    # changes what the property returns on the next call, so an inline assert at the
    # CATCHUP->LIVE transition below would read as a comparison between
    # non-overlapping literals -- a type-checker false positive, not a real one.
    phases = [clock.phase]
    assert clock.now() == boot - timedelta(hours=18)

    wall[0] = boot + timedelta(seconds=54)  # halfway
    phases.append(clock.phase)
    assert clock.now() < wall[0]

    wall[0] = boot + timedelta(seconds=200)  # past catch-up
    phases.append(clock.phase)
    assert clock.now() == wall[0]  # exactly 1.0, not approximately

    assert phases == [Phase.CATCHUP, Phase.CATCHUP, Phase.LIVE]

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
    """The autumn transition night (22:00-06:00 Europe/Berlin) runs 9 h instead of
    the usual 8 -- one component of the true worst case
    (test_the_default_depth_covers_every_boot_time_in_the_year), not the worst case
    itself, which turns out to be an early-morning boot rather than this evening
    one. This only pins that an evening boot that day reflects the extra hour."""
    evening_after_transition = datetime(2026, 10, 25, 21, 59, tzinfo=BERLIN)
    worst = required_history_depth(evening_after_transition)
    assert timedelta(hours=24) < worst <= timedelta(hours=26)


def test_the_default_depth_covers_every_boot_time_in_the_year() -> None:
    """required_history_depth is piecewise linear with slope 1 and resets at each
    local 06:00, so every day's maximum sits at that right-hand boundary. Probing
    one microsecond before each day's 06:00 -- the exact supremum of that day's
    piece -- rather than an hourly grid, which samples every piece's interior and
    would silently miss the true max. This is the test that produces the number
    Task 17 writes into §3.2; the lower bound pins that number as a measurement,
    not a constant that could be inflated arbitrarily while this still calls
    itself one."""
    worst = max(
        required_history_depth(
            datetime.combine(
                date(2026, 1, 1) + timedelta(days=d), time(6, 0), tzinfo=BERLIN
            )
            - timedelta(microseconds=1)
        )
        for d in range(366)
    )
    assert timedelta(hours=32) < worst <= ClockConfig.DEFAULT_HISTORY_DEPTH
