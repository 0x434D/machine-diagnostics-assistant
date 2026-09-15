"""§5.3's split: of the parts a condition touched, which are contained and which are not.

*"340 serials, 62 rejected, 278 shipped and need checking."* The whole value of
`/parts/affected` is in that second number, and it is the one a query that answered with a
total would have thrown away. A part that was rejected is already off the line; a part that
left as good is out in the world and someone has to go and look at it; a part with no
disposition at all is still between stations and is neither yet.

**The rule is one line and it lives here alone.** Written into the SQL it would have to be
written into the SQL of `/parts/affected`, `/lots/{lot_code}/parts` and
`/carriers/{id}/parts` separately, and the first of the three that got it wrong would
answer with a containment list that was quietly short.

Pure: rows in, counts and serials out. No database, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Final

REJECTED_DISPOSITION: Final = "reject"
"""What `part_dispositions.disposition` carries for a part the line threw out.

Anything else it carries means the part left the line intact — the plant writes `good`
today, and a third value arriving later is a part that *shipped*, which is the safe
direction to be wrong in: a containment list that names one part too many costs an
inspection, and one that names one too few costs whatever that part goes on to do.
"""


class Outcome(Enum):
    """Where a part went. Not a status the plant publishes — a reading of its disposition."""

    REJECTED = "rejected"
    SHIPPED = "shipped"
    ON_THE_LINE = "on_the_line"


def outcome_of(disposition: str | None) -> Outcome:
    """Which of the three a part with this disposition is.

    A null disposition is `ON_THE_LINE`: the part has no `part_dispositions` row, which is
    the ordinary state of every part between S2 and S3 at the instant a window closes. It
    is emphatically not "shipped" — assuming it left would put a part still inside the
    machine on a list of things to go and find.
    """
    if disposition is None:
        return Outcome.ON_THE_LINE
    if disposition == REJECTED_DISPOSITION:
        return Outcome.REJECTED
    return Outcome.SHIPPED


@dataclass(frozen=True)
class OutcomeGroup:
    """How many parts took this outcome, and enough of their serials to act on.

    `count` is exact and `serials` is capped, so a scope too long to list is still a number
    that can be trusted. `truncated` is what stops the capped list being read as the whole
    of it — and it is a field rather than a comparison a caller makes against the limit,
    because the caller would have to know the limit to make it.
    """

    count: int
    serials: tuple[str, ...]
    truncated: bool


@dataclass(frozen=True)
class Containment:
    """One containment scope, split three ways.

    `total` is the sum of the three counts, carried rather than left to be added up: it is
    the number quoted first in an answer, and deriving it from three fields is where a
    reader drops one of them.
    """

    total: int
    rejected: OutcomeGroup
    shipped: OutcomeGroup
    on_the_line: OutcomeGroup


def contain(
    parts: Iterable[tuple[str, str | None]], *, serial_limit: int
) -> Containment:
    """Split `(assembly_serial, disposition)` rows into §5.3's three groups.

    `parts` need not be sorted; the serials in each group come back sorted, so two calls
    over the same scope cite the same parts rather than whichever ones the plan returned
    first. Duplicate serials are not deduplicated — a caller handing the same part in twice
    has a query with a fan-out in it, and quietly absorbing that here would hide it.

    Raises ValueError if `serial_limit` is negative.
    """
    if serial_limit < 0:
        raise ValueError(f"serial_limit must not be negative; got {serial_limit!r}")

    collected: dict[Outcome, list[str]] = {outcome: [] for outcome in Outcome}
    for serial, disposition in parts:
        collected[outcome_of(disposition)].append(serial)

    groups = {
        outcome: _group(serials, serial_limit) for outcome, serials in collected.items()
    }
    return Containment(
        total=sum(group.count for group in groups.values()),
        rejected=groups[Outcome.REJECTED],
        shipped=groups[Outcome.SHIPPED],
        on_the_line=groups[Outcome.ON_THE_LINE],
    )


def _group(serials: list[str], serial_limit: int) -> OutcomeGroup:
    serials.sort()
    return OutcomeGroup(
        count=len(serials),
        serials=tuple(serials[:serial_limit]),
        truncated=len(serials) > serial_limit,
    )
