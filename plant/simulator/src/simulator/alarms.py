"""§4.2's alarms -- raise, acknowledge, clear -- and the reason this is the table most
likely to be mistaken for an answer.

**§3.3 rules out the obvious query in as many words.** Identifying the root cause as
"the first station that raised its own alarm" would be *circular*: this file is what
produces that, and an analysis reading it back would merely find what the simulator put
there. It does not apply at all to §3.5's scenarios 1 and 2, whose cause lies outside the
line, where no alarm is raised anywhere. The same warning is in `004_m2c.sql`, because a
reader of the schema has to meet it before writing their first query and will never open
this file.

**An alarm is raised on a measurement, never on a fault.** `ForceTolerance` reads the
peak S2 actually published and knows nothing about whether a scenario is running -- the
same rule `faults` states for the plant as a whole, and the whole of what makes the alarm
worth having. A raise keyed on "is the drift active" would be the injection written down
a second time under another name.

**The operator is part of §3.5's noise floor, not a convenience.** An alarm is
acknowledged after a delay drawn from `noise.NoiseFloor.acknowledge_delay_seconds`, the
station is restarted, and roughly one intervention in twelve carries a reset that changed
nothing (`noise.NoiseFloor.is_unnecessary_reset`). Nothing the operator does repairs a
drift, so §3.5 row 3 produces a *sequence* of shutdowns rather than one -- which is what a
press whose relief valve has drifted actually does to a shift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

from simulator.config import Settings
from simulator.events import ALARM
from simulator.noise import NoiseFloor

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    # Both are runtime import cycles and neither is needed at runtime: `line` is only
    # ever called through, and `StationNodes` is a Protocol. `stations.s2_joining`
    # imports this module, and `stations.base` imports `line`, so either edge taken for
    # real closes a loop.
    from simulator.line import Line
    from simulator.stations.base import StationNodes


@dataclass(frozen=True)
class AlarmCode:
    """One entry in the plant's alarm catalogue: what it is called, what it says, and
    how loud it is.

    None of the three is configuration in the sense §10.3 means. `code` and `text` are
    the alarm's identity -- §5.4's worked example names both -- and `severity` is a
    property of the condition rather than of a deployment: an installation that wants a
    different severity here wants a different alarm. The numbers that *are* configuration
    are the ones that decide when this fires, and they are in `Settings`.
    """

    code: str
    text: str
    severity: int


JOINING_FORCE_OUT_OF_TOLERANCE: Final = AlarmCode(
    "A-207", "joining force out of tolerance", 700
)
"""§5.4's worked derivation names this code and this text against an Aborted S2, so both
are quoted from the spec rather than invented here.

Severity 700 of OPC UA's 1-1000 band: high, and short of the 800+ a safety interlock
would carry. It is what makes the field carry information at all -- a catalogue whose
every entry was 1000 would be a column nothing could sort by.
"""

ALARM_CODES: Final[tuple[AlarmCode, ...]] = (JOINING_FORCE_OUT_OF_TOLERANCE,)
"""Every code the plant can raise. One today, and the tuple is what a reader of §3.7's
screen -- or of M4's `knowledge/alarms/`, which wants one document per code -- can
enumerate without reading the conditions that raise them."""


@dataclass
class Alarm:
    """One alarm instance, which is §5.2's `alarms` row before it has an id.

    Mutable, and the only mutable record in this module: an alarm *is* a lifecycle, and
    the three instants are filled by three different things at three different times. A
    frozen record replaced on each transition would make "the alarm on the screen" a
    different object from the one the operator is walking towards.

    `sequence` is this run's nth alarm, counting from zero. It keys the operator's
    draws (`noise.NoiseFloor.acknowledge_delay_seconds`), which is why it is a number the
    plant assigns rather than one the database does -- §5.2's `alarms.id` is the
    gateway's and this side never sees it.
    """

    sequence: int
    station: str
    code: AlarmCode
    raised_at: datetime
    acked_at: datetime | None = None
    cleared_at: datetime | None = None

    @property
    def active(self) -> bool:
        """An alarm is active until it is cleared, acknowledged or not. §4.2's two flags
        are independent, which is the whole reason there are two of them."""
        return self.cleared_at is None

    @property
    def acknowledged(self) -> bool:
        return self.acked_at is not None


class ForceTolerance:
    """S2's alarm condition: has the published peak left its band, and for how long.

    Pure -- no clock, no nodes, no PackML, and above all no `FaultSet`. It is handed the
    number S2 wrote to `JoiningForcePeak` and nothing else, so a run with a scenario and a
    run without one reach it through the same path and it cannot tell them apart. That is
    what stops the alarm being a second copy of the injection.

    The band comes from `Settings.joining_force_tolerance_newtons`, which is a multiple of
    the clamp's own part-to-part spread; the run length from
    `Settings.alarm_consecutive_parts`.
    """

    def __init__(self, settings: Settings) -> None:
        half = settings.joining_force_tolerance_newtons
        self._low = settings.joining_force_nominal - half
        self._high = settings.joining_force_nominal + half
        self._needed = settings.alarm_consecutive_parts
        self._consecutive = 0

    def observe(self, peak: float) -> bool:
        """True on the part that completes a run of `alarm_consecutive_parts` readings
        outside the band, False on every other part.

        The counter resets on the part that latches as well as on any part inside the
        band, so a station brought back up by an operator has to earn its next alarm with
        a fresh run rather than tripping on the first stroke.
        """
        if self._low <= peak <= self._high:
            self._consecutive = 0
            return False
        self._consecutive += 1
        if self._consecutive < self._needed:
            return False
        self._consecutive = 0
        return True


class AlarmSystem:
    """Every alarm this run has raised, the shutdowns they cause, and the operator who
    works through them.

    One object rather than three, because they are one mechanism and splitting them would
    mean a book that could hold an alarm nothing shut a station down for. It records the
    raise where the measurement is made -- S2 calls `observe_joining_force` from inside
    its own cycle -- and applies the PackML consequences afterwards, from `Line.step`,
    for a reason worth stating: driving a station's state machine from inside that
    station's own cycle would publish `Aborting` at the same instant as the `Unsuspending`
    the cycle had already written, and §5.2 keys `state_changes` on
    `(station_id, source_ts)`, so one of the two would silently be the other.

    `nodes` is how an alarm reaches the wire: §4.2 makes it an event, and it is published
    on the station that raised it.
    """

    def __init__(
        self, settings: Settings, seed: int, nodes: Mapping[str, StationNodes]
    ) -> None:
        self._settings = settings
        # Built here from (settings, seed) rather than threaded in, exactly as
        # `stations.base.Station` builds its own: every draw is a pure function of the
        # seed and its key, so two NoiseFloors built the same way are the same one.
        self._noise = NoiseFloor(settings, seed)
        self._nodes = nodes
        self._tolerances: dict[str, ForceTolerance] = {}
        self._alarms: list[Alarm] = []
        # Raised, and the station not yet shut down for it. Emptied by `settle`.
        self._shutdowns: list[int] = []
        # sequence -> the instant the operator reaches the panel. Not a queue, because
        # §3.7's acknowledge button moves one forward and a queue would have to be
        # re-sorted for it.
        self._due: dict[int, datetime] = {}

    @property
    def alarms(self) -> tuple[Alarm, ...]:
        """Every alarm this run has raised, oldest first."""
        return tuple(self._alarms)

    @property
    def active(self) -> tuple[Alarm, ...]:
        """The alarms §3.7's screen lists: raised and not yet cleared, oldest first."""
        return tuple(alarm for alarm in self._alarms if alarm.active)

    def observe_joining_force(self, station: str, at: datetime, peak: float) -> None:
        """One part's published `JoiningForcePeak`, from the station that published it.

        Called from inside S2's cycle and deliberately records rather than acts: see the
        class docstring for what driving PackML from here would do to `state_changes`.
        """
        if self._tolerance(station).observe(peak):
            self._raise(station, JOINING_FORCE_OUT_OF_TOLERANCE, at)

    def acknowledge(self, sequence: int, at: datetime) -> Alarm | None:
        """§3.7's acknowledge button: the operator is at the panel now rather than in
        `acknowledge_delay_seconds`' time.

        Returns the alarm, or None for a sequence this run never raised and for one whose
        intervention has already happened -- both of which are a screen a moment behind
        the line rather than an error.

        **It brings the intervention forward; it does not perform it.** What an operator
        pressing the button does to the line is the same thing the drawn delay does --
        acknowledge, restart, clear -- and two paths into that would be two chances for
        one of them to forget the restart and leave a station shut down with its alarm
        cleared.
        """
        if sequence not in self._due:
            return None
        self._due[sequence] = at
        return self._alarms[sequence]

    async def settle(self, line: Line, at: datetime) -> None:
        """Everything the alarms owe `line` at or before simulated instant `at`.

        Called from `Line.step` on **every** step, including the ones that produced
        nothing: a line whose stations are all starving behind an aborted S2 still has an
        operator walking towards it, and an intervention that only ran after a successful
        cycle would never run at all.
        """
        while self._shutdowns:
            alarm = self._alarms[self._shutdowns.pop(0)]
            await self._publish(alarm, alarm.raised_at)
            # §3.3: a fault shutdown, which is a cause candidate and does not clear
            # itself. Placed at the instant the condition was detected; `Line.abort`
            # moves it past anything the cycle already published for this station.
            await line.abort(alarm.station, alarm.raised_at)

        for sequence in sorted(seq for seq, due in self._due.items() if due <= at):
            await self._intervene(line, self._alarms[sequence], self._due.pop(sequence))

    def _tolerance(self, station: str) -> ForceTolerance:
        """This station's press monitor, made on first sight.

        Per station rather than one shared counter: a run of out-of-tolerance parts is a
        claim about one press, and a second joining station would otherwise complete the
        first one's run.
        """
        return self._tolerances.setdefault(station, ForceTolerance(self._settings))

    def _raise(self, station: str, code: AlarmCode, at: datetime) -> None:
        """Record a new alarm, unless this station already has one open under this code.

        A condition that is already reported is not a second alarm: without the check, a
        station brought back up into a still-drifted press would stack an unbounded list
        of open alarms on §3.7's screen, each with its own operator on the way.
        """
        if any(
            alarm.station == station and alarm.code is code and alarm.active
            for alarm in self._alarms
        ):
            return
        alarm = Alarm(len(self._alarms), station, code, at)
        self._alarms.append(alarm)
        self._shutdowns.append(alarm.sequence)
        self._due[alarm.sequence] = at + timedelta(
            seconds=self._noise.acknowledge_delay_seconds(alarm.sequence)
        )

    async def _intervene(self, line: Line, alarm: Alarm, at: datetime) -> None:
        """The operator: acknowledge, restart the station, and clear the alarm.

        `cleared_at` is the instant the station is back in `Execute` rather than the
        instant it was acknowledged, because that is when the condition this alarm
        reports -- a station shut down on its own fault -- has actually ended. The
        condition that *caused* it may well still be there, which is why a drift raises
        the next alarm three parts later instead of this one never clearing.
        """
        alarm.acked_at = at
        await self._publish(alarm, at)
        running = await line.restart(alarm.station, at)
        alarm.cleared_at = running
        await self._publish(alarm, running)
        if self._noise.is_unnecessary_reset(alarm.sequence):
            # §3.5's "occasional unnecessary reset": six more rows in `state_changes` and
            # nothing else -- the station is back in Execute before any cycle can observe
            # it elsewhere. It is noise an analysis has to be able to dismiss, which is
            # exactly what §3.5 asks the noise floor for.
            await line.reset_in_place(alarm.station, running)

    async def _publish(self, alarm: Alarm, at: datetime) -> None:
        """§4.2's event, on the station that raised it.

        Raises KeyError for a station this system was given no nodes for, which is a
        plant wired so that an alarm it can raise cannot be published -- loud, because
        the alternative is an alarm on §3.7's screen that never reaches the history the
        diagnosis is made from.
        """
        await self._nodes[alarm.station].trigger_event(
            ALARM,
            at,
            {
                "AlarmCode": alarm.code.code,
                "AlarmText": alarm.code.text,
                "AlarmSeverity": alarm.code.severity,
                "AlarmRaisedAt": alarm.raised_at,
                "AlarmActive": alarm.active,
                "AlarmAcknowledged": alarm.acknowledged,
            },
        )
