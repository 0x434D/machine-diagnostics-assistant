"""§3.1's identities. Components belong to lots; assemblies belong to themselves."""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from simulator.config import Settings
from simulator.identity import (
    LANES,
    Component,
    LotSchedule,
    assembly_serial,
    component_serial,
    load_carrier,
)

T0 = datetime(2026, 9, 14, 6, 0, tzinfo=UTC)
TAKT = timedelta(seconds=6)


def schedule(settings: Settings | None = None) -> LotSchedule:
    """A schedule at T0, on the shipped configuration unless a test needs its own."""
    return LotSchedule(settings or Settings(), T0)


def test_a_component_serial_names_its_lane_and_is_unique() -> None:
    """Lane is in the serial because containment questions start from a lane."""
    assert component_serial(lane=1, index=42) == "C-1-00000042"
    assert component_serial(lane=2, index=42) == "C-2-00000042"

    lots = schedule()
    drawn = [lots.draw(lane, T0 + i * TAKT).serial for i in range(50) for lane in LANES]
    assert len(set(drawn)) == len(drawn)
    assert {serial.split("-")[1] for serial in drawn} == {str(lane) for lane in LANES}


def test_an_assembly_serial_keeps_m1s_format() -> None:
    """A ruling, not a preference: the gateway, the analysis service and §6.3's worked
    chat citation all resolve `A-00088431` already, so a cosmetic change here breaks a
    citation that works."""
    assert assembly_serial(0) == "A-00000000"
    assert assembly_serial(88431) == "A-00088431"


def test_two_lanes_draw_from_different_lots_at_the_same_instant() -> None:
    """§3.5 scenario 7 contaminates ONE lane's lot. If both lanes shared a lot the
    scenario could not be expressed, and scenario 5 (lane contamination) collapses
    into it."""
    lots = schedule(Settings(lot_size=7))
    for i in range(50):
        at = T0 + i * TAKT
        codes = {lane: lots.draw(lane, at).lot_code for lane in LANES}
        assert len(set(codes.values())) == len(LANES), (
            f"lanes shared a lot code at {at}: {codes}"
        )

    # Nor may a code reappear once a lane has moved on: a second appearance would
    # silently merge two containment lists into one.
    every_lot = [lot for lane in LANES for lot in lots.lots(lane)]
    assert len(every_lot) > len(LANES), "the run must be long enough to roll a lot over"
    assert len({lot.lot_code for lot in every_lot}) == len(every_lot)


def test_a_lot_is_exhausted_before_the_next_one_starts() -> None:
    """Lots are consumed in order and depleted_at is when the next began. An
    overlapping schedule would make 'which lot was this component from' ambiguous
    at exactly the moment a containment query needs it."""
    size = 5
    lots = schedule(Settings(lot_size=size))
    for i in range(3 * size):
        lots.draw(1, T0 + i * TAKT)

    history = lots.lots(1)
    # Three, not four: the next lot is loaded at the instant its first component is
    # drawn, not at the instant the previous one ran out. That laziness is what makes
    # the windows contiguous -- depleting eagerly would leave the gap between the last
    # draw of one lot and the first of the next covered by no lot at all, which is a
    # `lot_at` with no answer for an instant the line was producing in.
    assert len(history) == 3
    assert history[0].loaded_at == T0
    for earlier, later in itertools.pairwise(history):
        assert earlier.depleted_at == later.loaded_at
    assert history[-1].depleted_at is None, "the lot in use is not yet depleted"


def test_a_lot_supplies_exactly_lot_size_components() -> None:
    """The number M2c's scenario 7 containment list is scored against. One off here is
    one part in the wrong containment list, which is the failure §1 measures."""
    size = 5
    lots = schedule(Settings(lot_size=size))
    drawn = [lots.draw(2, T0 + i * TAKT) for i in range(2 * size)]
    per_lot = {lot_code: 0 for lot_code in {c.lot_code for c in drawn}}
    for component in drawn:
        per_lot[component.lot_code] += 1
    assert sorted(per_lot.values()) == [size, size]


def test_the_lot_a_component_came_from_is_recoverable_from_its_read_time() -> None:
    """§3.4a's association, asked the other way round: a containment query that starts
    from an instant must land on the same lot the component itself records."""
    lots = schedule(Settings(lot_size=4))
    drawn = [(lots.draw(1, T0 + i * TAKT), T0 + i * TAKT) for i in range(12)]
    for component, at in drawn:
        assert lots.lot_at(1, at).lot_code == component.lot_code


def test_an_assembly_records_the_components_it_was_built_from() -> None:
    """As-built, at the instant of assembly. §3.4a: the association is known exactly
    at the instant of production and only approximately afterwards."""
    lots = schedule()
    assembly = load_carrier(lots, index=3, carrier_id=7, at=T0)

    assert assembly.serial == "A-00000003"
    assert assembly.carrier_id == 7
    assert assembly.created_at == T0
    assert len(assembly.components) == len(LANES)
    assert [component.lane for component in assembly.components] == list(LANES)
    assert all(component.read_at == T0 for component in assembly.components)
    assert {component.lot_code for component in assembly.components} == {
        lots.current(lane).lot_code for lane in LANES
    }


def test_two_assemblies_never_share_a_component() -> None:
    """Genealogy is exact or it is decoration: one component sits in exactly one
    assembly, which is what §6.4's single-component recall query resolves."""
    lots = schedule(Settings(lot_size=3))
    assemblies = [
        load_carrier(lots, index=i, carrier_id=i % 4, at=T0 + i * TAKT)
        for i in range(20)
    ]
    serials = [c.serial for assembly in assemblies for c in assembly.components]
    assert len(set(serials)) == len(serials)


def test_the_same_seed_produces_the_same_lots_and_serials() -> None:
    """§3.6, and the property M2a had to fix once already — seed derivation must not
    use hash(), which is PYTHONHASHSEED-salted."""
    assert _run(schedule(Settings(seed=20260914))) == _run(
        schedule(Settings(seed=20260914))
    )
    assert _run(schedule(Settings(seed=20260914))) != _run(
        schedule(Settings(seed=20260915))
    )


def test_a_lot_schedule_survives_a_change_of_python_hash_seed() -> None:
    """The M2a defect, in the only form that can catch it: `str.__hash__` is salted per
    interpreter process, so a hash()-derived seed is stable inside one pytest run and
    different on the next boot. Three processes, three salts, one answer."""
    codes = {_lot_codes_under(hash_seed) for hash_seed in ("0", "1", "12345")}
    assert len(codes) == 1, f"the lot schedule moved with PYTHONHASHSEED: {codes}"


def _run(lots: LotSchedule) -> list[tuple[str, str, str]]:
    """What a run looks like from the outside: every serial, lot and supplier it issued."""
    drawn: list[Component] = []
    for i in range(40):
        drawn.extend(load_carrier(lots, i, i % 4, T0 + i * TAKT).components)
    suppliers = {
        lot.lot_code: lot.supplier for lane in LANES for lot in lots.lots(lane)
    }
    return [(c.serial, c.lot_code, suppliers[c.lot_code]) for c in drawn]


_PROBE = """
import json
from datetime import UTC, datetime

from simulator.config import Settings
from simulator.identity import LANES, LotSchedule, load_carrier

lots = LotSchedule(Settings(), datetime(2026, 9, 14, 6, tzinfo=UTC))
at = datetime(2026, 9, 14, 6, tzinfo=UTC)
built = [load_carrier(lots, i, 0, at).components for i in range(3)]
print(json.dumps([
    [(c.serial, c.lot_code) for parts in built for c in parts],
    [lots.current(lane).supplier for lane in LANES],
]))
"""


def _lot_codes_under(hash_seed: str) -> str:
    done = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        pytest.fail(f"probe failed under PYTHONHASHSEED={hash_seed}: {done.stderr}")
    return json.dumps(json.loads(done.stdout))
