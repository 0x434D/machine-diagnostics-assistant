"""S2 Joining: presses the parts together (§3.1), against the serial (§3.4a).

The press is force-clamped at the top and position-stopped at the bottom, so the two
numbers §4.1 publishes as streams come from two different places: the peak is read off
the trace the load cell produced, and the joining distance is the stop the ram ran to.
Neither of them knows where the components met the ram, which is the whole reason
§3.4a keeps the curve.

**The press is recorded against the serial, at the instant it happens.** The two
streams stay -- they are the trend data -- but the per-part record is authoritative for
the part, and reconstructing it later by asking which part was at S2 at 02:14:07 is the
inference §3.4a forbids and what makes a containment list unusable at the moment it
matters.
"""

from __future__ import annotations

from datetime import datetime
from typing import override

from simulator.alarms import AlarmSystem
from simulator.carriers import Carrier
from simulator.config import Settings
from simulator.curve import force_distance, peak_of, work_of
from simulator.events import PART_PROCESSED
from simulator.faults import JOINING_CLAMP_FORCE, NO_FAULTS, PRESS_CONTACT, FaultSet
from simulator.identity import LANES
from simulator.line import PartState
from simulator.stations.base import Station, StationNodes, require_assembly


class JoiningStation(Station):
    def __init__(
        self,
        nodes: StationNodes,
        settings: Settings,
        seed: int,
        alarms: AlarmSystem,
        *,
        faults: FaultSet = NO_FAULTS,
    ) -> None:
        """`alarms` is §4.2's alarm system, and it has no default.

        The press is the one station with an alarm condition today, and a default would
        make a JoiningStation built without one a press that cannot report itself out of
        tolerance -- which is §3.5 row 3's whole first half, absent with nothing raised.
        The station hands it a measurement and nothing else; what an alarm then does to
        the line is `AlarmSystem`'s and `Line`'s.
        """
        super().__init__(nodes, settings, seed, faults=faults)
        self._alarms = alarms

    @override
    async def on_part(self, at: datetime, carrier: Carrier, part: PartState) -> None:
        assembly = require_assembly(part, carrier, self.code)
        settings = self._settings

        # The three knobs §3.4a separates, drawn per part. The clamp is the press's own
        # (M2c's scenario 3 drifts it), the contact point is the components' (scenario 7
        # moves it) and the stiffness is the material's. Only the first reaches a
        # published scalar.
        # The drift is applied to the drawn clamp, not to the mean it was drawn from:
        # the draw then happens identically whether or not scenario 3 is running, which
        # is what keeps a loaded-but-unfired scenario byte-identical to no scenario at
        # all. It reaches `JoiningForcePeak` only through the trace `force_distance`
        # builds below, so the published peak stays a measurement of the press rather
        # than a second copy of the injected number.
        clamp = self._faults.modify(
            JOINING_CLAMP_FORCE,
            self._rng.gauss(
                settings.joining_force_nominal, settings.joining_force_sigma
            ),
            at,
        )
        contact_mm = self._rng.gauss(
            settings.press_contact_nominal_mm, settings.press_contact_sigma_mm
        )
        # Once per lane, threading the result through: the ram meets the stack of both
        # components, so an undersized one on either lane pushes contact later by its own
        # shortfall and two undersized lanes add up. Applied after the draw for the same
        # reason the clamp's drift is, and the two lanes are asked about separately
        # because §3.5's scenario 7 delivers its bad lot to exactly one of them.
        for lane in LANES:
            contact_mm = self._faults.modify(PRESS_CONTACT, contact_mm, at, lane=lane)
        stiffness = self._rng.gauss(
            settings.press_stiffness_nominal, settings.press_stiffness_sigma
        )
        curve = force_distance(
            self._rng,
            settings,
            contact_mm=contact_mm,
            clamp_force=clamp,
            stiffness=stiffness,
        )

        # Read off the trace, not handed back from the draw that set the clamp: what a
        # load cell reports is the clamp plus whatever it made of it, and a part that
        # met the ram later spends fewer samples clamped.
        peak = round(peak_of(curve), 2)
        # The stop draw, and deliberately not a feature of the curve. `curve.distance_of`
        # was deleted for the reason that nothing happening at constant ram position can
        # appear in a force-against-position trace: the stop is where the trace ends,
        # not something inside it. The sigma here is the position sensor's measurement
        # noise, because a hard stop is a hard stop.
        distance = round(
            self._rng.gauss(
                settings.joining_distance_nominal, settings.joining_distance_sigma
            ),
            4,
        )

        # What the press actually left in the joint, carried to S3 on the part. The area
        # under the trace is the one statistic that falls both when the clamp drifts down
        # (a lower plateau) and when the components are undersized (a shorter one), while
        # the two published scalars move for the first and not at all for the second --
        # which is what makes §3.5's scenarios 3 and 7 separable while their symptom is
        # the same rising `gap`. `Settings.gap_work_exponent` is where it becomes one.
        part.joining_work = work_of(curve, settings)

        await self._nodes.write("JoiningForcePeak", at, peak)
        await self._nodes.write("JoiningDistance", at, distance)
        # §4.2's alarm, read off the number that was just published rather than off the
        # clamp that was drawn or off the FaultSet that may have moved it. That is what
        # keeps the alarm a measurement: §3.3 warns that "the first station to raise an
        # alarm" is a circular root cause, and an alarm raised from the injection would
        # make it circular in the plant as well as in the query. Recording only -- the
        # shutdown is applied after the cycle, from `Line.step`.
        self._alarms.observe_joining_force(self.code, at, peak)
        await self._nodes.trigger_event(
            PART_PROCESSED,
            at,
            {
                "AssemblySerial": assembly.serial,
                # Unrounded, unlike the two scalars: the curve is integrated (see
                # `curve.work_of`, the statistic the two scalars cannot produce) rather
                # than displayed, and it is an event field, so none of the repeat-value
                # coalescing that makes rounding matter on a historised stream applies.
                "Curve": list(curve),
                # The same two numbers the streams above carry, to the digit, so the
                # per-part record and the time series can never be read as two
                # different measurements of one press.
                "PeakForce": peak,
                "JoiningDistance": distance,
            },
        )
