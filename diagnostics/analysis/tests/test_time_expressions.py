"""Tests for the time-expression resolver (spec §6.1 step 2, §3.2).

Pure unit tests: no database, no container, just constructed `datetime`s. Each test builds
`now` from a Berlin-local instant chosen to be unambiguous (never inside the autumn
02:00–03:00 fold) and converts to UTC before calling `resolve`, matching the contract that
`now` is UTC-aware.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from analysis.time_expressions import UNDERSTOOD_EXPRESSIONS, resolve
from analysis.windows import Window

BERLIN = ZoneInfo("Europe/Berlin")


def _berlin(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """A UTC instant expressed as a Berlin wall-clock time, for readable test inputs."""
    return datetime(year, month, day, hour, minute, tzinfo=BERLIN).astimezone(UTC)


def test_last_night_at_05_00_is_the_shift_that_ended_yesterday_not_the_running_one() -> (
    None
):
    """The rule "last night" exists for: at 05:00 the caller is *inside* a night shift,
    so the most recent *completed* one ended at 06:00 yesterday and began at 22:00 the day
    before — not the shift currently running, which has not ended yet."""
    now = _berlin(2026, 9, 15, 5, 0)
    resolution = resolve("last night", now)

    assert resolution is not None
    assert resolution.window == Window.utc(
        _berlin(2026, 9, 13, 22, 0), _berlin(2026, 9, 14, 6, 0)
    )
    assert resolution.closed is True


def test_last_night_at_21_59_is_the_night_that_ended_this_morning() -> None:
    now = _berlin(2026, 9, 15, 21, 59)
    resolution = resolve("last night", now)

    assert resolution is not None
    assert resolution.window == Window.utc(
        _berlin(2026, 9, 14, 22, 0), _berlin(2026, 9, 15, 6, 0)
    )
    assert resolution.closed is True


def test_autumn_dst_night_is_nine_hours() -> None:
    """Germany falls back on 2026-10-25 (03:00 -> 02:00). The night shift that starts the
    evening before (2026-10-24 22:00) lives through the fold and runs nine hours, not
    the nominal eight — resolved here by asking for "last night" just after it ends."""
    now = _berlin(2026, 10, 25, 8, 0)
    resolution = resolve("last night", now)

    assert resolution is not None
    assert resolution.window == Window.utc(
        _berlin(2026, 10, 24, 22, 0), _berlin(2026, 10, 25, 6, 0)
    )
    assert resolution.window.duration_seconds == 9 * 3600


def test_spring_dst_night_is_seven_hours() -> None:
    """Germany springs forward on 2026-03-29 (02:00 -> 03:00), inside the night shift
    that started the evening before (2026-03-28 22:00), which loses an hour to seven."""
    now = _berlin(2026, 3, 29, 8, 0)
    resolution = resolve("last night", now)

    assert resolution is not None
    assert resolution.window == Window.utc(
        _berlin(2026, 3, 28, 22, 0), _berlin(2026, 3, 29, 6, 0)
    )
    assert resolution.window.duration_seconds == 7 * 3600


def test_this_shift_at_14_00_00_exactly_is_late_not_early() -> None:
    """Windows are half-open: the instant a shift starts belongs to it, so 14:00:00 is
    late (14-22), never the tail end of early (06-14)."""
    now = _berlin(2026, 9, 15, 14, 0)
    resolution = resolve("this shift", now)

    assert resolution is not None
    assert resolution.window == Window.utc(
        _berlin(2026, 9, 15, 14, 0), _berlin(2026, 9, 15, 22, 0)
    )
    assert resolution.closed is False


def test_last_shift_at_14_00_00_exactly_is_early_the_shift_that_just_ended() -> None:
    now = _berlin(2026, 9, 15, 14, 0)
    resolution = resolve("last shift", now)

    assert resolution is not None
    assert resolution.window == Window.utc(
        _berlin(2026, 9, 15, 6, 0), _berlin(2026, 9, 15, 14, 0)
    )
    assert resolution.closed is True


def test_this_shift_one_microsecond_before_14_00_is_still_early() -> None:
    now = _berlin(2026, 9, 15, 13, 59)
    resolution = resolve("this shift", now)

    assert resolution is not None
    assert resolution.window == Window.utc(
        _berlin(2026, 9, 15, 6, 0), _berlin(2026, 9, 15, 14, 0)
    )


def test_today_and_yesterday_are_adjacent_local_calendar_days() -> None:
    now = _berlin(2026, 9, 15, 12, 0)

    today = resolve("today", now)
    yesterday = resolve("yesterday", now)

    assert today is not None
    assert yesterday is not None
    assert today.window == Window.utc(
        _berlin(2026, 9, 15, 0, 0), _berlin(2026, 9, 16, 0, 0)
    )
    assert yesterday.window == Window.utc(
        _berlin(2026, 9, 14, 0, 0), _berlin(2026, 9, 15, 0, 0)
    )
    assert today.closed is False
    assert yesterday.closed is True
    # The two windows share the boundary instant: yesterday ends exactly where today starts.
    assert yesterday.window.to_ts == today.window.from_ts


def test_today_is_23_hours_on_the_spring_forward_day() -> None:
    now = _berlin(2026, 3, 29, 12, 0)
    resolution = resolve("today", now)

    assert resolution is not None
    assert resolution.window.duration_seconds == 23 * 3600


def test_yesterday_is_25_hours_on_the_day_after_fall_back() -> None:
    now = _berlin(2026, 10, 26, 12, 0)
    resolution = resolve("yesterday", now)

    assert resolution is not None
    assert resolution.window.duration_seconds == 25 * 3600


def test_last_hour_is_exactly_sixty_real_minutes_across_the_autumn_fold() -> None:
    """The fold hour is where wall-clock timedelta arithmetic on an aware datetime would
    go wrong; "last hour" must still measure sixty real minutes through it."""
    now = _berlin(2026, 10, 25, 8, 0)
    resolution = resolve("last hour", now)

    assert resolution is not None
    assert resolution.window.to_ts == now
    assert resolution.window.duration_seconds == 3600
    assert resolution.closed is True


def test_last_24_hours_ends_at_now_and_spans_exactly_a_day_of_real_time() -> None:
    now = _berlin(2026, 9, 15, 10, 30)
    resolution = resolve("last 24 hours", now)

    assert resolution is not None
    assert resolution.window.to_ts == now
    assert resolution.window.duration_seconds == 24 * 3600


def test_this_week_and_last_week_are_adjacent_monday_to_monday() -> None:
    # 2026-09-15 is a Tuesday.
    now = _berlin(2026, 9, 15, 10, 0)

    this_week = resolve("this week", now)
    last_week = resolve("last week", now)

    assert this_week is not None
    assert last_week is not None
    assert this_week.window == Window.utc(
        _berlin(2026, 9, 14, 0, 0), _berlin(2026, 9, 21, 0, 0)
    )
    assert last_week.window == Window.utc(
        _berlin(2026, 9, 7, 0, 0), _berlin(2026, 9, 14, 0, 0)
    )
    assert last_week.window.to_ts == this_week.window.from_ts
    assert this_week.closed is False
    assert last_week.closed is True


def test_unparseable_expression_returns_none() -> None:
    assert resolve("the third tuesday of never", _berlin(2026, 9, 15, 10, 0)) is None


def test_understood_expressions_is_non_empty_and_listable() -> None:
    assert UNDERSTOOD_EXPRESSIONS
    assert "last night" in UNDERSTOOD_EXPRESSIONS
    assert all(isinstance(item, str) for item in UNDERSTOOD_EXPRESSIONS)


@pytest.mark.parametrize(
    "raw",
    ["Last Night", "  last night  ", "LAST NIGHT", "\tlast night\n"],
)
def test_matching_is_case_and_whitespace_tolerant(raw: str) -> None:
    now = _berlin(2026, 9, 15, 10, 0)
    assert resolve(raw, now) == resolve("last night", now)


def test_naive_now_is_rejected_rather_than_silently_misresolved() -> None:
    # The naive datetime is the thing under test: `resolve` must reject it rather than
    # silently guess a zone for it, so DTZ001 does not apply here.
    naive_now = datetime(2026, 9, 15, 10, 0)  # noqa: DTZ001
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve("today", naive_now)


@pytest.mark.parametrize("expression", sorted(UNDERSTOOD_EXPRESSIONS))
def test_every_understood_expression_resolves_to_a_forward_utc_window(
    expression: str,
) -> None:
    """Every returned window is UTC-aware and strictly forward. `Window.__post_init__`
    enforces both already, so a resolver that ever built one wrongly fails here loudly
    rather than silently — this test exists to exercise that guarantee across the whole
    set, not to re-implement it."""
    now = _berlin(2026, 9, 15, 10, 0)
    resolution = resolve(expression, now)

    assert resolution is not None
    assert resolution.window.from_ts.tzinfo is UTC
    assert resolution.window.to_ts.tzinfo is UTC
    assert resolution.window.from_ts < resolution.window.to_ts
