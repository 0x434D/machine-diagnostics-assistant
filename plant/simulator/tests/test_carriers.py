"""§3.1's circulating carrier pool."""

from __future__ import annotations

from simulator.carriers import CarrierPool


def test_the_pool_hands_out_the_carriers_it_was_built_with() -> None:
    pool = CarrierPool(count=3)
    assert pool.count == 3
    assert pool.available == 3
    first = pool.acquire()
    assert first is not None
    assert pool.available == 2


def test_an_exhausted_pool_reports_none_rather_than_inventing_a_carrier() -> None:
    """S1 with no free carrier must stop, not conjure one. A pool that grows on
    demand would make the carrier count decorative."""
    pool = CarrierPool(count=1)
    assert pool.acquire() is not None
    assert pool.acquire() is None


def test_carriers_circulate_rather_than_passing_once() -> None:
    """§3.1: 'Carriers circulate -- without that, a worn carrier passes once and the
    carrier-wear scenario has no statistical signal to find.' Over four times the
    pool size, every carrier must be used a comparable number of times."""
    pool = CarrierPool(count=4)
    seen: list[int] = []
    for _ in range(16):
        carrier = pool.acquire()
        assert carrier is not None
        seen.append(carrier.carrier_id)
        pool.release(carrier)

    counts = {carrier_id: seen.count(carrier_id) for carrier_id in range(4)}
    assert set(counts) == {0, 1, 2, 3}
    assert max(counts.values()) - min(counts.values()) <= 1


def test_a_released_carrier_goes_to_the_back_of_the_queue() -> None:
    """FIFO rather than LIFO: a stack would recirculate one carrier while the rest
    sat idle, which is the same statistical blindness as not circulating at all."""
    pool = CarrierPool(count=3)
    first = pool.acquire()
    assert first is not None
    pool.release(first)
    second = pool.acquire()
    assert second is not None
    assert second.carrier_id != first.carrier_id
