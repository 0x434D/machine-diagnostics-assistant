"""§3.1's buffers, and the starved/blocked rule §3.3 derives from them."""

from __future__ import annotations

import pytest
from simulator.buffers import Buffer, suspend_reason_for
from simulator.carriers import Carrier


def test_a_buffer_knows_which_stations_it_sits_between() -> None:
    """§4.1: buffer nodes reference the stations they sit between, and the gateway
    reads those references to discover topology. A buffer that did not carry them
    would make the topology configured rather than discovered."""
    buffer = Buffer("B2_3", capacity=5, upstream="S2", downstream="S3")
    assert (buffer.upstream, buffer.downstream) == ("S2", "S3")


def test_taking_from_an_empty_buffer_is_refused() -> None:
    """A level that can go negative is a level that stops meaning anything."""
    buffer = Buffer("B1_2", capacity=5, upstream="S1", downstream="S2")
    with pytest.raises(ValueError, match="empty"):
        buffer.take()


def test_putting_into_a_full_buffer_is_refused() -> None:
    buffer = Buffer("B1_2", capacity=2, upstream="S1", downstream="S2")
    buffer.put(Carrier(0))
    buffer.put(Carrier(1))
    with pytest.raises(ValueError, match="full"):
        buffer.put(Carrier(2))


def test_a_buffer_returns_carriers_in_the_order_it_received_them() -> None:
    """A buffer is a queue on a conveyor, not a stack. LIFO here would let one
    carrier sit at the bottom for the whole run, which is the same statistical
    blindness as carriers that do not circulate at all."""
    buffer = Buffer("B1_2", capacity=3, upstream="S1", downstream="S2")
    buffer.put(Carrier(7))
    buffer.put(Carrier(9))
    assert buffer.take().carrier_id == 7
    assert buffer.take().carrier_id == 9


def test_an_empty_upstream_buffer_starves_the_station_below_it() -> None:
    upstream = Buffer("B2_3", capacity=5, upstream="S2", downstream="S3")
    reason = suspend_reason_for(upstream=upstream, downstream=None)
    assert reason is not None
    assert (reason.direction, reason.buffer_id) == ("starved", "B2_3")


def test_a_full_downstream_buffer_blocks_the_station_above_it() -> None:
    downstream = Buffer("B3_4", capacity=1, upstream="S3", downstream="S4")
    downstream.put(Carrier(0))
    reason = suspend_reason_for(upstream=None, downstream=downstream)
    assert reason is not None
    assert (reason.direction, reason.buffer_id) == ("blocked", "B3_4")


def test_starvation_is_reported_before_blockage_when_both_hold() -> None:
    """Both can be true at once on a line that has just stopped. The station has no
    part to work on either way, and 'starved' names the condition that arrived
    first -- reporting 'blocked' would point diagnosis downstream of a line that is
    actually short of parts."""
    upstream = Buffer("B2_3", capacity=5, upstream="S2", downstream="S3")
    downstream = Buffer("B3_4", capacity=1, upstream="S3", downstream="S4")
    downstream.put(Carrier(0))
    reason = suspend_reason_for(upstream=upstream, downstream=downstream)
    assert reason is not None
    assert reason.direction == "starved"


def test_a_station_with_work_and_room_has_no_reason_to_suspend() -> None:
    upstream = Buffer("B2_3", capacity=5, upstream="S2", downstream="S3")
    upstream.put(Carrier(0))
    downstream = Buffer("B3_4", capacity=5, upstream="S3", downstream="S4")
    assert suspend_reason_for(upstream=upstream, downstream=downstream) is None


def test_the_ends_of_the_line_have_no_buffer_on_their_outer_side() -> None:
    """S1 is fed by lanes and S4 discharges to outfeed, so each has one side with no
    buffer at all. Neither may be reported as suspended for a buffer that is not
    there -- M2c's scenarios 1 and 2 are precisely faults on those outer sides."""
    assert suspend_reason_for(upstream=None, downstream=None) is None
