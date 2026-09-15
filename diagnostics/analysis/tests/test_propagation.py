"""§5.4's propagation walk, one test per category and two on what it refuses to do.

The fixtures are state episodes and buffer levels. **Alarms appear in this file only as
local data the assertions read** — never as an argument to anything under test — because
the discredited heuristic §3.3 and `004_m2c.sql` both warn about is a query over exactly
that table, and a module that cannot see alarms cannot be tempted by them.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

from analysis.propagation import (
    DEFAULT_LEAD_IN,
    BufferLink,
    BufferSample,
    LineTopology,
    StationEpisode,
    Termination,
    derive_chain,
)

TOPOLOGY = LineTopology(
    (
        BufferLink("B1_2", upstream_station="S1", downstream_station="S2", capacity=5),
        BufferLink("B2_3", upstream_station="S2", downstream_station="S3", capacity=5),
        BufferLink("B3_4", upstream_station="S3", downstream_station="S4", capacity=5),
    )
)
"""§3.1's line, in the shape `read.buffers` hands it over: one row per buffer naming the
station on each side. Nothing here says S1 is first — that falls out of S1 being the only
station no buffer discharges into, which is what makes the head and tail of the line
discovered rather than written down."""


def _at(minute: int, second: int) -> datetime:
    return datetime(2026, 9, 12, 2, minute, second, tzinfo=UTC)


def _levels(buffer: str, *samples: tuple[datetime, int]) -> list[BufferSample]:
    return [BufferSample(buffer, at, level) for at, level in samples]


def _worked_example() -> tuple[list[StationEpisode], list[BufferSample]]:
    """§5.4's own example: S2 aborts, B2_3 drains, S3 starves, B3_4 drains, S4 starves."""
    episodes = [
        StationEpisode("S2", "Aborted", _at(13, 40), None),
        StationEpisode("S3", "Suspended", _at(14, 2), None, "starved:B2_3", "B2_3"),
        StationEpisode("S4", "Suspended", _at(14, 32), None, "starved:B3_4", "B3_4"),
    ]
    levels = [
        *_levels(
            "B2_3",
            (_at(13, 40), 4),
            (_at(13, 46), 3),
            (_at(13, 52), 2),
            (_at(13, 58), 1),
            (_at(14, 2), 0),
            (_at(14, 20), 0),
        ),
        *_levels(
            "B3_4",
            (_at(14, 2), 5),
            (_at(14, 8), 4),
            (_at(14, 14), 3),
            (_at(14, 20), 2),
            (_at(14, 26), 1),
            (_at(14, 32), 0),
            (_at(14, 50), 0),
        ),
    ]
    return episodes, levels


def test_a_chain_ending_at_an_aborted_station_is_internal() -> None:
    episodes, levels = _worked_example()

    derivation = derive_chain(
        station="S4",
        at=_at(14, 40),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert [link.station for link in derivation.links] == ["S4", "S3", "S2"]
    assert derivation.links[0].buffer == "B3_4"
    assert derivation.links[0].buffer_condition_since == _at(14, 32)
    assert derivation.links[1].buffer == "B2_3"
    assert derivation.links[1].buffer_condition_since == _at(14, 2)
    assert derivation.links[2].buffer is None
    assert derivation.termination is Termination.CAUSE_CANDIDATE
    assert derivation.category == "internal"
    assert derivation.root is not None
    assert derivation.root.station == "S2"
    assert derivation.root.episode.state == "Aborted"


def test_a_chain_ending_at_a_held_station_is_internal() -> None:
    """§3.3 makes `Held` a cause candidate for the same reason `Aborted` is: it needs an
    operator and does not clear itself."""
    episodes = [
        StationEpisode("S2", "Held", _at(13, 40), _at(20, 0), "jam"),
        StationEpisode("S3", "Suspended", _at(14, 2), None, "starved:B2_3", "B2_3"),
    ]
    levels = _levels("B2_3", (_at(13, 40), 2), (_at(13, 52), 1), (_at(14, 2), 0))

    derivation = derive_chain(
        station="S3",
        at=_at(14, 10),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert derivation.category == "internal"
    assert derivation.root is not None
    assert derivation.root.episode.state == "Held"


def test_a_chain_ending_at_the_head_of_the_line_is_external_upstream() -> None:
    """§3.5's scenario 1: the feed upstream of S1 has run dry. `starved:feeder` names no
    buffer, S1 has none above it, and there is nothing on the line left to blame."""
    episodes = [
        StationEpisode("S1", "Suspended", _at(5, 0), None, "starved:feeder"),
        StationEpisode("S2", "Suspended", _at(5, 30), None, "starved:B1_2", "B1_2"),
        StationEpisode("S3", "Suspended", _at(6, 0), None, "starved:B2_3", "B2_3"),
    ]
    levels = [
        *_levels(
            "B1_2",
            (_at(5, 0), 3),
            (_at(5, 10), 2),
            (_at(5, 20), 1),
            (_at(5, 30), 0),
            (_at(5, 40), 0),
        ),
        *_levels(
            "B2_3",
            (_at(5, 30), 3),
            (_at(5, 40), 2),
            (_at(5, 50), 1),
            (_at(6, 0), 0),
        ),
    ]

    derivation = derive_chain(
        station="S3",
        at=_at(6, 10),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert [link.station for link in derivation.links] == ["S3", "S2", "S1"]
    assert derivation.termination is Termination.LINE_EDGE
    assert derivation.category == "external_upstream"
    assert derivation.root is not None
    assert derivation.root.episode.reason == "starved:feeder"
    assert derivation.cause_candidates == ()


def test_a_chain_ending_at_the_tail_of_the_line_is_external_downstream() -> None:
    """§3.5's scenario 2: the outfeed below S4 has stopped taking parts. The walk runs
    the other way — a full buffer is explained by the station that draws from it."""
    episodes = [
        StationEpisode("S4", "Suspended", _at(20, 0), None, "blocked:outfeed"),
        StationEpisode("S3", "Suspended", _at(20, 30), None, "blocked:B3_4", "B3_4"),
    ]
    levels = _levels(
        "B3_4",
        (_at(20, 0), 2),
        (_at(20, 10), 3),
        (_at(20, 20), 4),
        (_at(20, 30), 5),
        (_at(20, 40), 5),
    )

    derivation = derive_chain(
        station="S3",
        at=_at(20, 40),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert [link.station for link in derivation.links] == ["S3", "S4"]
    assert derivation.links[0].buffer == "B3_4"
    assert derivation.links[0].buffer_condition_since == _at(20, 30)
    assert derivation.termination is Termination.LINE_EDGE
    assert derivation.category == "external_downstream"


def test_two_independent_cause_candidates_are_ambiguous() -> None:
    """S3 aborted and recovered while B3_4 was draining, and S2 aborted and did not.
    Both explain part of what S4 sees. §5.4 keeps the field rather than silently
    picking one, so the chain is still returned — with both candidates on it."""
    episodes, levels = _worked_example()
    episodes.append(StationEpisode("S3", "Aborted", _at(13, 0), _at(13, 30)))

    derivation = derive_chain(
        station="S4",
        at=_at(14, 40),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert derivation.category == "ambiguous"
    assert [episode.station for episode in derivation.cause_candidates] == ["S3", "S2"]
    assert [link.station for link in derivation.links] == ["S4", "S3", "S2"]


def test_a_buffer_that_never_ran_empty_does_not_explain_anything() -> None:
    """S4 reports `starved:B3_4` and B3_4 never fell below two. The walk has nowhere
    to go and says so; inventing the next link would be the failure this whole module
    exists to avoid."""
    episodes = [
        StationEpisode("S4", "Suspended", _at(14, 32), None, "starved:B3_4", "B3_4"),
    ]
    levels = _levels(
        "B3_4", (_at(14, 2), 3), (_at(14, 14), 2), (_at(14, 26), 2), (_at(14, 32), 2)
    )

    derivation = derive_chain(
        station="S4",
        at=_at(14, 40),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert derivation.termination is Termination.UNEXPLAINED
    assert derivation.category is None
    assert derivation.root is None
    assert derivation.unexplained is not None
    assert derivation.unexplained.buffer == "B3_4"
    assert [link.station for link in derivation.links] == ["S4"]
    assert derivation.links[0].buffer_condition_since is None


def test_a_buffer_condition_no_station_episode_covers_is_not_explained() -> None:
    """B3_4 did run empty, and S3 was executing throughout. The line has no episode
    that accounts for it, which is a different dead end from the one above and must
    also not be filled in."""
    episodes = [
        StationEpisode("S4", "Suspended", _at(14, 32), None, "starved:B3_4", "B3_4"),
        StationEpisode("S3", "Execute", _at(10, 0), None),
    ]
    levels = _levels(
        "B3_4", (_at(14, 2), 3), (_at(14, 14), 2), (_at(14, 26), 1), (_at(14, 32), 0)
    )

    derivation = derive_chain(
        station="S4",
        at=_at(14, 40),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert derivation.termination is Termination.UNEXPLAINED
    assert derivation.category is None
    assert derivation.unexplained is not None
    assert derivation.unexplained.station == "S3"
    assert derivation.links[0].buffer_condition_since == _at(14, 32)


def test_the_root_is_not_the_first_station_to_raise_an_alarm() -> None:
    """**The circularity guard.**

    §5.4's worked example, with one thing added that a real window always has: an
    unrelated alarm still open at S4, raised before anything else in this window. The
    discredited heuristic — "the root is the first station that raised its own alarm" —
    answers S4, three stations *downstream* of the fault. The walk answers S2, because
    it followed buffers and never looked at an alarm.

    §3.3 calls that heuristic circular and `004_m2c.sql`'s header repeats the warning:
    the simulator writes the alarm and the shutdown from the same injected fault, so an
    analysis that read it back would be graded on finding what it was handed.
    """
    alarms_in_the_window = (
        ("S4", _at(10, 0), "A-901 outfeed photo-eye dirty"),
        ("S2", _at(13, 40), "A-207 joining force out of tolerance"),
    )
    first_alarm = min(alarms_in_the_window, key=lambda alarm: alarm[1])
    assert first_alarm[0] == "S4"

    episodes, levels = _worked_example()
    derivation = derive_chain(
        station="S4",
        at=_at(14, 40),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert derivation.root is not None
    assert derivation.root.station == "S2"
    assert derivation.root.station != first_alarm[0]
    assert derivation.category == "internal"


def test_the_walk_takes_no_alarm_input_at_all() -> None:
    """The guard above proves the answer does not depend on alarms for one fixture.
    This one proves it cannot for any: there is no argument through which an alarm
    could reach the walk."""
    parameters = inspect.signature(derive_chain).parameters

    assert not [name for name in parameters if "alarm" in name.lower()]
    assert not [
        parameter
        for parameter in parameters.values()
        if "alarm" in str(parameter.annotation).lower()
    ]


def test_the_lead_in_is_a_parameter() -> None:
    """§10.3: every number is configuration.

    The lead-in is how far back the walk looks, and it has to be a real horizon rather
    than "all of history": §4.1 publishes a buffer `Level` only when a carrier moves
    through it, so a line that has stopped moving publishes nothing at all and the
    sample that proves B3_4 ran empty is the last one before the stoppage. A lead-in
    shorter than that silence does not reach it, and the walk says it could not
    continue rather than reaching further for something to blame.
    """
    episodes = [
        StationEpisode("S4", "Suspended", _at(14, 32), None, "starved:B3_4", "B3_4"),
        StationEpisode("S3", "Aborted", _at(11, 0), None),
    ]
    levels = _levels("B3_4", (_at(11, 30), 2), (_at(11, 45), 1), (_at(12, 0), 0))

    assert DEFAULT_LEAD_IN >= timedelta(minutes=5)
    reaching = derive_chain(
        station="S4",
        at=_at(14, 40),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert reaching.category == "internal"
    assert reaching.root is not None
    assert reaching.root.station == "S3"

    short = derive_chain(
        station="S4",
        at=_at(14, 40),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
        lead_in=timedelta(seconds=30),
    )

    assert short.termination is Termination.UNEXPLAINED
    assert short.category is None


def _empty_carrier_pool() -> tuple[list[StationEpisode], list[BufferSample]]:
    """S3 jams and holds its carriers; B3_4 drains, S4 starves, and S1 runs out of
    free carriers to start a part on. §3.1 circulates the carriers in a closed loop —
    "without that, a worn carrier passes once and the carrier-wear scenario has no
    statistical signal to find" — so an empty pool is a statement about the tail of the
    line, never about a supplier."""
    episodes = [
        StationEpisode("S1", "Suspended", _at(14, 0), None, "starved:carrier-return"),
        StationEpisode("S4", "Suspended", _at(13, 50), None, "starved:B3_4", "B3_4"),
        StationEpisode("S3", "Held", _at(13, 20), None, "jam"),
    ]
    levels = _levels(
        "B3_4", (_at(13, 30), 2), (_at(13, 40), 1), (_at(13, 50), 0), (_at(13, 58), 0)
    )
    return episodes, levels


def test_an_empty_carrier_pool_is_explained_by_whoever_holds_the_carriers() -> None:
    """The walk continues through the carrier loop instead of stopping at S1.

    The loop's upstream is the *tail* of the line, so the chain leaves S1 and arrives at
    S4, and from there the ordinary buffer walk reaches the station actually holding the
    carriers. Answering "external_upstream" here would send an operator to inspect a
    feeder that is full while S3 sits jammed.
    """
    episodes, levels = _empty_carrier_pool()

    derivation = derive_chain(
        station="S1",
        at=_at(14, 5),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert [link.station for link in derivation.links] == ["S1", "S4", "S3"]
    assert derivation.links[0].buffer == "carrier-return"
    assert derivation.links[0].buffer_condition_since == _at(14, 0)
    assert derivation.links[1].buffer == "B3_4"
    assert derivation.termination is Termination.CAUSE_CANDIDATE
    assert derivation.category == "internal"
    assert derivation.root is not None
    assert derivation.root.station == "S3"


def test_a_dry_feeder_and_an_empty_carrier_pool_are_different_facts() -> None:
    """Both are S1 `Suspended` and `starved`, and §5.4's rule as written — "the chain
    ends at S1 starved" — categorises them the same. They are not the same: components
    that did not arrive are outside the line, and carriers that did not come back are
    inside it. Asserted together so the two branches cannot quietly collapse into one.
    """
    dry_feeder = [
        StationEpisode("S1", "Suspended", _at(14, 0), None, "starved:feeder"),
    ]
    from_the_feeder = derive_chain(
        station="S1",
        at=_at(14, 5),
        episodes=dry_feeder,
        levels=(),
        topology=TOPOLOGY,
    )

    episodes, levels = _empty_carrier_pool()
    from_the_loop = derive_chain(
        station="S1",
        at=_at(14, 5),
        episodes=episodes,
        levels=levels,
        topology=TOPOLOGY,
    )

    assert from_the_feeder.termination is Termination.LINE_EDGE
    assert from_the_feeder.category == "external_upstream"
    assert from_the_loop.category != from_the_feeder.category
    assert from_the_loop.category == "internal"


def test_an_unexplained_carrier_shortage_is_not_a_supplier_problem() -> None:
    """Nothing below S1 accounts for where the carriers went. That is a chain that
    could not be completed, and saying so is the answer — falling back to
    `external_upstream` would name the one place the carriers provably are not."""
    episodes = [
        StationEpisode("S1", "Suspended", _at(14, 0), None, "starved:carrier-return"),
        StationEpisode("S4", "Execute", _at(10, 0), None),
    ]

    derivation = derive_chain(
        station="S1",
        at=_at(14, 5),
        episodes=episodes,
        levels=(),
        topology=TOPOLOGY,
    )

    assert derivation.termination is Termination.UNEXPLAINED
    assert derivation.category is None
    assert derivation.unexplained is not None
    assert derivation.unexplained.station == "S4"
    assert derivation.unexplained.buffer == "carrier-return"


def test_an_empty_carrier_pool_below_a_blocked_outfeed_is_external_downstream() -> None:
    """The carrier loop leads to the tail, and the tail is `blocked:outfeed` — §3.5's
    scenario 2, which is where the carriers are: parked on a line that cannot discharge.

    Not `internal`, although the chain entered the loop: the category is derived from
    where the chain *terminates*, and §3.3 makes a `Suspended` station a consequence
    rather than a cause however the chain arrived at it. Reporting `internal` here would
    send an operator into a line whose problem is below it.
    """
    episodes = [
        StationEpisode("S1", "Suspended", _at(14, 0), None, "starved:carrier-return"),
        StationEpisode("S4", "Suspended", _at(13, 40), None, "blocked:outfeed"),
    ]

    derivation = derive_chain(
        station="S1",
        at=_at(14, 5),
        episodes=episodes,
        levels=(),
        topology=TOPOLOGY,
    )

    assert [link.station for link in derivation.links] == ["S1", "S4"]
    assert derivation.termination is Termination.LINE_EDGE
    assert derivation.category == "external_downstream"
