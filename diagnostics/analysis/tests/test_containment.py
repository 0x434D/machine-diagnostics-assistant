"""The three-way split, and the one of the three that is the point.

*"340 serials, 62 rejected, 278 shipped and need checking."* The second number is already
contained; the third is what someone has to act on tonight. Pure, so the rule is stated once
and every containment endpoint inherits it — a rule written into three WHERE clauses would
have a first one to be edited alone.
"""

from __future__ import annotations

import pytest
from analysis.containment import Outcome, contain, outcome_of


def test_a_part_with_no_disposition_is_still_on_the_line() -> None:
    """Not shipped, which is the reading that matters: assuming it left would put a part
    still inside the machine on a list of things to go and find."""
    assert outcome_of(None) is Outcome.ON_THE_LINE


def test_a_rejected_part_is_contained_and_anything_else_left_the_line() -> None:
    assert outcome_of("reject") is Outcome.REJECTED
    assert outcome_of("good") is Outcome.SHIPPED


def test_a_disposition_nobody_has_seen_before_counts_as_shipped() -> None:
    """The safe direction to be wrong in. A containment list naming one part too many costs
    an inspection; one naming a part too few costs whatever that part goes on to do."""
    assert outcome_of("returned-to-stores") is Outcome.SHIPPED


def test_the_split_counts_exactly_and_the_total_is_carried() -> None:
    parts = [
        ("A-1", "good"),
        ("A-2", "reject"),
        ("A-3", None),
        ("A-4", "good"),
    ]

    containment = contain(parts, serial_limit=10)

    assert containment.total == 4
    assert containment.rejected.count == 1
    assert containment.shipped.count == 2
    assert containment.on_the_line.count == 1


def test_the_counts_stay_exact_when_the_serial_list_is_capped() -> None:
    """A scope too long to list is still a number that can be trusted, and `truncated` is
    what stops the capped list being mistaken for the whole of it."""
    containment = contain(
        [(f"A-{index}", "good") for index in range(50)], serial_limit=3
    )

    assert containment.shipped.count == 50
    assert len(containment.shipped.serials) == 3
    assert containment.shipped.truncated is True
    assert containment.rejected.truncated is False


def test_the_serials_come_back_sorted_rather_than_in_query_order() -> None:
    """Two calls over the same scope must cite the same parts. SQL promises no order without
    an ORDER BY, so a capped list taken in plan order would name a different three each time
    the planner changed its mind."""
    shuffled = [("A-9", "good"), ("A-1", "good"), ("A-5", "good")]

    assert contain(shuffled, serial_limit=2).shipped.serials == ("A-1", "A-5")


def test_an_empty_scope_is_three_empty_groups_and_not_an_error() -> None:
    containment = contain([], serial_limit=5)

    assert containment.total == 0
    assert containment.rejected.serials == ()
    assert containment.shipped.truncated is False


def test_a_negative_limit_is_a_caller_bug_and_is_raised() -> None:
    with pytest.raises(ValueError, match="serial_limit"):
        contain([], serial_limit=-1)
