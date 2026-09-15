"""Resolves a handful of fixed time phrases against the Europe/Berlin shift calendar.

Spec §6.1 step 2: the model reads "last night" out of a sentence but does not compute
what it means — date arithmetic across shift boundaries and DST is what models are
unreliable at and code is exact at. This module is that code half. It is pure: no HTTP,
no database, no I/O, and it takes `now` as a parameter rather than reading a clock, so a
caller supplies the injected clock and every answer here is reproducible from its inputs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from analysis.windows import Window

EARLY_SHIFT_START_HOUR = 6
LATE_SHIFT_START_HOUR = 14
NIGHT_SHIFT_START_HOUR = 22
"""Shift boundaries (§3.2): early 06–14, late 14–22, night 22–06, all Europe/Berlin.

Module constants rather than `Settings` fields. §10.3 makes every number configuration,
but these three are a shared *semantic* definition the plant simulator also holds — putting
one copy in the analysis service's config would let the two drift into two sources of truth
across the one boundary (OPC UA) the architecture allows anything to cross.
"""

BERLIN = ZoneInfo("Europe/Berlin")

ShiftKind = Literal["early", "late", "night"]


@dataclass(frozen=True)
class Resolution:
    """What a supported time expression resolved to.

    `label` is a human-readable statement of the concrete window, for an answer or an error
    to cite back to the user. `closed` is `window.is_closed(now)` at resolution time — the
    window will never later become open, but `now` moves, so a caller must not cache this
    flag past the call that produced it.
    """

    window: Window
    label: str
    closed: bool


def _local(day: date, hour: int) -> datetime:
    """A Berlin-local instant at `hour`:00 on `day`.

    Builds a fresh aware datetime from calendar fields rather than adding a `timedelta` to
    an existing one — timedelta arithmetic on a zoneinfo-aware datetime shifts the wall
    clock, not elapsed real time, and silently gives the wrong instant across a DST
    transition. Every caller in this module reaches DST-safe day arithmetic by adding
    `timedelta` to a plain `date` first and localizing the result here, never by shifting an
    already-aware datetime.

    `datetime.combine` defaults to `fold=0`, the first occurrence of an ambiguous local time.
    Berlin's ambiguous hour is 02:00–03:00 on the autumn transition; the only hours this
    function is ever called with are 00:00, 06:00, 14:00 and 22:00, none of which falls
    inside it, so fold=0 is always the unambiguous, correct instant here. That stops being
    true the moment the shift boundaries become configurable enough to land in
    [02:00, 03:00) — worth knowing before someone makes them a `Settings` field.
    """
    return datetime.combine(day, time(hour=hour), tzinfo=BERLIN)


def _current_shift(local_now: datetime) -> tuple[ShiftKind, datetime, datetime]:
    """The shift containing `local_now`. Half-open, so an instant exactly on a boundary
    belongs to the shift that starts there, never the one that just ended."""
    today = local_now.date()
    hour = local_now.hour
    if EARLY_SHIFT_START_HOUR <= hour < LATE_SHIFT_START_HOUR:
        return (
            "early",
            _local(today, EARLY_SHIFT_START_HOUR),
            _local(today, LATE_SHIFT_START_HOUR),
        )
    if LATE_SHIFT_START_HOUR <= hour < NIGHT_SHIFT_START_HOUR:
        return (
            "late",
            _local(today, LATE_SHIFT_START_HOUR),
            _local(today, NIGHT_SHIFT_START_HOUR),
        )
    if hour >= NIGHT_SHIFT_START_HOUR:
        tomorrow = today + timedelta(days=1)
        return (
            "night",
            _local(today, NIGHT_SHIFT_START_HOUR),
            _local(tomorrow, EARLY_SHIFT_START_HOUR),
        )
    yesterday = today - timedelta(days=1)
    return (
        "night",
        _local(yesterday, NIGHT_SHIFT_START_HOUR),
        _local(today, EARLY_SHIFT_START_HOUR),
    )


def _previous_shift(
    kind: ShiftKind, start: datetime
) -> tuple[ShiftKind, datetime, datetime]:
    """The shift immediately before the one that starts at `start`."""
    if kind == "early":
        yesterday = start.date() - timedelta(days=1)
        return "night", _local(yesterday, NIGHT_SHIFT_START_HOUR), start
    if kind == "late":
        return "early", _local(start.date(), EARLY_SHIFT_START_HOUR), start
    return "late", _local(start.date(), LATE_SHIFT_START_HOUR), start


def _last_completed_night(local_now: datetime) -> tuple[datetime, datetime]:
    """The most recent night shift whose end is at or before `local_now`.

    "Last night" is the most recent *completed* night shift, not the one currently running:
    at 05:00 `local_now` sits inside a night shift, and that shift's own end (06:00) is
    still ahead of it, so it does not qualify — the answer is the one before it.

    At most three steps through `_previous_shift`: night recurs every third shift, so
    walking back from whichever shift contains `local_now` reaches a night shift's end at
    or before `local_now` within one full early/late/night cycle.
    """
    kind, start, end = _current_shift(local_now)
    while not (kind == "night" and end <= local_now):
        kind, start, end = _previous_shift(kind, start)
    return start, end


def _week_start(local_now: datetime) -> date:
    """The Monday of the ISO week containing `local_now`, as a date."""
    return local_now.date() - timedelta(days=local_now.weekday())


def _shift_label(kind: ShiftKind, start: datetime, end: datetime) -> str:
    return f"{kind} shift {start:%Y-%m-%d %H:%M} – {end:%Y-%m-%d %H:%M} Europe/Berlin"


def _resolve_last_night(local_now: datetime) -> tuple[Window, str]:
    start, end = _last_completed_night(local_now)
    return Window.utc(start, end), _shift_label("night", start, end)


def _resolve_this_shift(local_now: datetime) -> tuple[Window, str]:
    kind, start, end = _current_shift(local_now)
    return Window.utc(start, end), _shift_label(kind, start, end)


def _resolve_last_shift(local_now: datetime) -> tuple[Window, str]:
    kind, start, _end = _current_shift(local_now)
    prev_kind, prev_start, prev_end = _previous_shift(kind, start)
    return Window.utc(prev_start, prev_end), _shift_label(
        prev_kind, prev_start, prev_end
    )


def _resolve_today(local_now: datetime) -> tuple[Window, str]:
    today = local_now.date()
    start = _local(today, 0)
    end = _local(today + timedelta(days=1), 0)
    return Window.utc(start, end), f"today {today.isoformat()} Europe/Berlin"


def _resolve_yesterday(local_now: datetime) -> tuple[Window, str]:
    yesterday = local_now.date() - timedelta(days=1)
    start = _local(yesterday, 0)
    end = _local(yesterday + timedelta(days=1), 0)
    return Window.utc(start, end), f"yesterday {yesterday.isoformat()} Europe/Berlin"


def _resolve_last_hour(local_now: datetime) -> tuple[Window, str]:
    # Real elapsed time, not wall clock: computed from the UTC instant so an hour
    # straddling a Berlin DST transition still measures sixty real minutes.
    now = local_now.astimezone(UTC)
    start = now - timedelta(hours=1)
    return Window.utc(
        start, now
    ), f"last hour {start:%Y-%m-%d %H:%M} – {now:%Y-%m-%d %H:%M} UTC"


def _resolve_last_24_hours(local_now: datetime) -> tuple[Window, str]:
    now = local_now.astimezone(UTC)
    start = now - timedelta(hours=24)
    return (
        Window.utc(start, now),
        f"last 24 hours {start:%Y-%m-%d %H:%M} – {now:%Y-%m-%d %H:%M} UTC",
    )


def _resolve_this_week(local_now: datetime) -> tuple[Window, str]:
    monday = _week_start(local_now)
    start = _local(monday, 0)
    end = _local(monday + timedelta(days=7), 0)
    return Window.utc(
        start, end
    ), f"this week, starting {monday.isoformat()} Europe/Berlin"


def _resolve_last_week(local_now: datetime) -> tuple[Window, str]:
    monday = _week_start(local_now) - timedelta(days=7)
    start = _local(monday, 0)
    end = _local(monday + timedelta(days=7), 0)
    return Window.utc(
        start, end
    ), f"last week, starting {monday.isoformat()} Europe/Berlin"


_RESOLVERS: dict[str, Callable[[datetime], tuple[Window, str]]] = {
    "last night": _resolve_last_night,
    "this shift": _resolve_this_shift,
    "last shift": _resolve_last_shift,
    "today": _resolve_today,
    "yesterday": _resolve_yesterday,
    "last hour": _resolve_last_hour,
    "last 24 hours": _resolve_last_24_hours,
    "this week": _resolve_this_week,
    "last week": _resolve_last_week,
}

UNDERSTOOD_EXPRESSIONS: frozenset[str] = frozenset(_RESOLVERS)
"""Every expression `resolve` understands, so a caller can list them in an error rather
than guess at what would have worked. Derived from `_RESOLVERS` rather than listed a
second time, so the two cannot drift apart."""


def resolve(expression: str, now: datetime) -> Resolution | None:
    """Resolve a fixed time phrase to a concrete UTC window against the Berlin shift
    calendar, as of `now`.

    Assumes `now` is timezone-aware (the invariant: all timestamps UTC). Matching is
    case-insensitive and tolerant of surrounding whitespace; `expression` is otherwise
    matched verbatim against `UNDERSTOOD_EXPRESSIONS` — there is no fuzzy matching, because
    a near-miss guessed wrong is worse than a clean "not understood".

    Returns `None` for anything not in `UNDERSTOOD_EXPRESSIONS`. Never guesses: an
    unrecognised expression is this function's ordinary, by-design output, not a bug to
    raise about.

    Raises `ValueError` if `now` is naive — a caller passing wall-clock time without a zone
    would otherwise get a window silently computed against the wrong instant.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"now must be timezone-aware; got {now!r}")
    resolver = _RESOLVERS.get(expression.strip().lower())
    if resolver is None:
        return None
    local_now = now.astimezone(BERLIN)
    window, label = resolver(local_now)
    return Resolution(window=window, label=label, closed=window.is_closed(now))
