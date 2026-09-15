"""§3.5's six faults, as modifiers on the numbers they change.

**A fault is a modifier, not a branch.** It changes a number where that number is
computed -- the clamp force S2 draws, the level S1 publishes, the propensity a defect
draw is compared against -- so nothing downstream ever learns that a fault exists. The
alternative, `if fault_active:` at each station, puts scenario knowledge in four places,
makes two simultaneous faults ambiguous, and lets a reader tell a fault run from a clean
one by reading the plant rather than its output. §3.5's premise is that the analysis has
to find the fault from its consequences, so the consequences have to arise from the same
code path as normal behaviour.

**Nothing in this module draws a random number.** A fault moves the *parameter* of a
distribution and the plant's own seeded draw samples from it. That is what makes the
identity property below structural rather than something a test can only probe at: a
fault cannot shift any station's RNG stream, because it never touches one. It is also
why every call site applies `modify` to the value it has already drawn rather than to
the mean it is about to draw from -- the draw happens whether or not a fault is active,
so the stream is the same either way.

**The identity property.** A fault outside its window returns its input unchanged, so a
run with a scenario loaded but not yet fired is byte-identical to a run with no scenario
at all. That is what makes the ground-truth log's timestamps mean anything: the instant
recorded there is the first instant at which the plant's output could differ from a
clean run's.

`until` is when the fault is **repaired**, not when it stops being interesting. The
quantity snaps back to nominal, which is slightly unphysical for a drift -- a relief
valve that has drifted stays drifted until someone turns it back -- and is the price of
the identity property holding at both ends of the window rather than only at the start.
A scenario that wants a permanent drift leaves `until` at None.

**Magnitudes are ends, not rates.** A fault ramps from nominal to its magnitude over
`ramp_seconds` and then holds. A rate has no ceiling: a joining-force drift of
-120 N/h drives the 4200 N clamp through zero thirty-five hours later and takes
`curve.force_distance`'s geometry check with it, hours after the injection, in a
traceback that names no fault.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final


class FaultKind(StrEnum):
    """§3.5's six, plus the one its own scenario table needs and its fault list omits.

    **The spec is short one fault, and this says so rather than working around it.**
    §3.5 lists six available faults and then eight scenarios; rows 7 and 8 are
    "lane 1 receives lot L-4471 with undersized components" and "one single defective
    component reaches the line", and neither is expressible as any of the six. Lane
    contamination is the nearest, and §3.5 fixes its classes as `missing_part` +
    `contamination` on row 5 -- using it for row 7 would make scenarios 5 and 7 the same
    fault, which is the collapse `DEFECT_CLASSES_BY_KIND` exists to prevent.

    So `UNDERSIZED_COMPONENTS` is the seventh, and it is a component fault rather than a
    machine one: it moves the press's contact point (§3.4a's components' knob) and
    nothing else. Rows 7 and 8 differ only in its magnitude and how long its window is.
    A scenario composes these seven; it does not add an eighth.
    """

    FEEDER_STARVATION = "feeder_starvation"
    OUTFEED_BLOCKAGE = "outfeed_blockage"
    JOINING_FORCE_DRIFT = "joining_force_drift"
    CARRIER_WEAR = "carrier_wear"
    LANE_CONTAMINATION = "lane_contamination"
    OPTICS_FOULING = "optics_fouling"
    UNDERSIZED_COMPONENTS = "undersized_components"


# The quantities a fault may modify -- one name per place in the plant where a number a
# fault changes is computed. Strings rather than an enum because they cross into the
# ground-truth log (§3.6) and out of it again, and a name that survives JSON unchanged
# is one fewer thing to keep in step.
LANE_FILL: Final = "lane_fill"
"""S1's `LaneFill_n`, in units. Scenario 1 takes it to zero."""
OUTFEED_FILL: Final = "outfeed_fill"
"""S4's `OutfeedFill`, in parts. Scenario 2 fills it past capacity."""
JOINING_CLAMP_FORCE: Final = "joining_clamp_force"
"""The press's clamp setting in newtons, drawn per part at S2 -- **not** the peak read
off the trace. Scenario 3 drifts the setting; the peak follows because the trace is
generated from it, which is what keeps `JoiningForcePeak` a measurement rather than a
second copy of the fault."""
DEFECT_PROPENSITY: Final = "defect_propensity"
"""The probability, for one part and one defect class, that the class is present.
Scenarios 4 and 5 raise it -- each for the two classes §3.5 names and nothing else."""
OPTICS_CLARITY: Final = "optics_clarity"
"""How much of the camera's nominal contrast survives to the image, 1.0 when clean.
Scenario 6 (D8) lowers it, and the classifier's confidence falls out of the image."""
PRESS_CONTACT: Final = "press_contact"
"""Where the ram meets resistance, in millimetres of travel from the top of the stroke.

§3.4a's components' knob. Scenarios 7 and 8 push it later -- an undersized component
lets the ram travel further before it meets anything -- and it moves **neither**
published scalar: the clamp still caps the load and the ram still runs to the same stop.
It is visible in the shape of the trace, and in the joining work that falls out of it,
which is what makes scenario 7 separable from scenario 3 at all."""

# The context keys a call site supplies. Context is what a modifier reads to decide
# whether it applies to *this* part -- see `FaultSet.modify`.
LANE: Final = "lane"
CARRIER_ID: Final = "carrier_id"
DEFECT_CLASS: Final = "defect_class"

RAMP_SECONDS: Final = "ramp_seconds"
"""The one parameter every fault accepts: how long it takes to reach its magnitude.
Absent or zero means a step."""


class _Form(StrEnum):
    """The two arithmetics a fault may have. Two and not six, because a modifier that
    each fault wrote for itself is six places for the composition rule to differ."""

    SCALE = "scale"
    OFFSET = "offset"


@dataclass(frozen=True)
class _KindSpec:
    """What a fault kind modifies, how, and what it reads to decide whether it applies.

    `scope_param` names the parameter carrying *which* carrier or lane the fault is on,
    and `scope_context` the context key it is matched against. `scope_required` is the
    difference between scenario 4 and a line-wide drift: a carrier-wear fault that may
    omit its carrier is a fault that silently wears every carrier, and scenario 4's
    entire diagnostic value is that it concentrates.
    """

    quantity: str
    form: _Form
    magnitude: str
    scope_param: str | None = None
    scope_context: str | None = None
    scope_required: bool = False
    defect_classes: tuple[str, ...] = ()


_SPECS: Final[Mapping[FaultKind, _KindSpec]] = {
    FaultKind.FEEDER_STARVATION: _KindSpec(
        LANE_FILL, _Form.SCALE, "factor", "lane", LANE
    ),
    FaultKind.OUTFEED_BLOCKAGE: _KindSpec(OUTFEED_FILL, _Form.OFFSET, "parts"),
    FaultKind.JOINING_FORCE_DRIFT: _KindSpec(
        JOINING_CLAMP_FORCE, _Form.OFFSET, "newtons"
    ),
    FaultKind.CARRIER_WEAR: _KindSpec(
        DEFECT_PROPENSITY,
        _Form.SCALE,
        "factor",
        "carrier",
        CARRIER_ID,
        scope_required=True,
        defect_classes=("misalignment", "scratch"),
    ),
    FaultKind.LANE_CONTAMINATION: _KindSpec(
        DEFECT_PROPENSITY,
        _Form.SCALE,
        "factor",
        "lane",
        LANE,
        scope_required=True,
        defect_classes=("missing_part", "contamination"),
    ),
    FaultKind.OPTICS_FOULING: _KindSpec(OPTICS_CLARITY, _Form.SCALE, "factor"),
    FaultKind.UNDERSIZED_COMPONENTS: _KindSpec(
        PRESS_CONTACT,
        _Form.OFFSET,
        "millimetres",
        "lane",
        LANE,
        scope_required=True,
    ),
}

DEFECT_CLASSES_BY_KIND: Final[Mapping[FaultKind, tuple[str, ...]]] = {
    kind: spec.defect_classes for kind, spec in _SPECS.items() if spec.defect_classes
}
"""§3.5's table, in one place: which defect classes each fault raises.

A property of the kind rather than a parameter, because §3.5 fixes it -- carrier wear
produces `misalignment` and `scratch`, lane contamination `missing_part` and
`contamination` -- and a scenario free to choose would be free to make scenario 4 and
scenario 5 indistinguishable. The names are literals here rather than imported from
`inspection_client.DEFECT_CLASSES`: that module imports `stations.base`, which imports
this one, so the import would be a cycle. `test_faults` asserts the two agree.
"""

_CONTEXT_KEYS: Final[Mapping[str, tuple[str, ...]]] = {
    LANE_FILL: (LANE,),
    OUTFEED_FILL: (),
    JOINING_CLAMP_FORCE: (),
    DEFECT_PROPENSITY: (CARRIER_ID, LANE, DEFECT_CLASS),
    OPTICS_CLARITY: (),
    PRESS_CONTACT: (LANE,),
}
"""What a call site must supply for each quantity, checked on **every** call.

Checked always rather than only when a fault that needs it is active. A propensity call
that forgot its carrier would otherwise work perfectly until scenario 4 fired, and then
raise inside a run that has already written history -- or, worse, in a version that
skipped instead of raising, apply carrier 7's wear to every part and turn the scenario
into the line-wide drift it exists to be distinguished from.
"""

QUANTITIES: Final[tuple[str, ...]] = tuple(_CONTEXT_KEYS)
"""Every quantity a fault may modify. `modify` refuses anything else, so a mistyped
quantity at a call site is an exception rather than a modifier that never fires."""


@dataclass(frozen=True)
class Fault:
    """One injection: what, when it starts, how hard, and when it is repaired.

    `at` and `until` are offsets from the run's origin (`FaultSet`), not instants:
    §3.5's scenario is a list of `(offset, fault, params)` and a scenario written against
    wall-clock instants could only be run once.

    Raises ValueError for a window that cannot happen, a parameter this kind does not
    take, a missing magnitude, and a scope this kind requires but the params omit.
    """

    kind: FaultKind
    at: timedelta
    params: Mapping[str, float]
    until: timedelta | None = None

    def __post_init__(self) -> None:
        spec = _SPECS[self.kind]
        # A defensive copy, because a scenario's params end up in the ground-truth log
        # (§3.6) and a caller that mutated the mapping afterwards would make the log
        # describe a run that did not happen.
        object.__setattr__(self, "params", dict(self.params))

        if self.at < timedelta(0):
            raise ValueError(
                f"{self.kind} fires at {self.at}, before the run starts: an offset is "
                "measured from the origin the FaultSet is built with"
            )
        if self.until is not None and self.until <= self.at:
            # The trap this catches: `until` reads naturally as a duration, and a
            # scenario that meant "ten minutes long" and wrote `until=timedelta(
            # minutes=10)` against `at=timedelta(hours=2)` gets a fault that is never
            # active for one instant -- injected, logged as ground truth, and with no
            # consequence anywhere for anything to find.
            raise ValueError(
                f"{self.kind} is repaired at {self.until}, at or before it fires at "
                f"{self.at}: both are offsets from the run origin, so `until` is the "
                "instant the fault is cleared and not how long it lasts"
            )

        allowed = {spec.magnitude, RAMP_SECONDS}
        if spec.scope_param is not None:
            allowed.add(spec.scope_param)
        unknown = sorted(set(self.params) - allowed)
        if unknown:
            # Refused rather than ignored: pydantic-settings already has to tolerate
            # unknown keys in `.env` (see `config.Settings`), and this project has
            # shipped one config mapping keyed on the wrong spelling, where every
            # consumer silently took the default. A scenario parameter is the same
            # shape of mistake with the same silence.
            raise ValueError(
                f"{self.kind} takes {sorted(allowed)}, not {unknown}: a parameter this "
                "fault does not read is a magnitude that was meant to change something "
                "and changes nothing"
            )
        if spec.magnitude not in self.params:
            raise ValueError(
                f"{self.kind} needs a {spec.magnitude!r} parameter: a fault with no "
                "magnitude is a window in the ground-truth log with nothing under it"
            )
        if spec.scope_required and spec.scope_param not in self.params:
            raise ValueError(
                f"{self.kind} needs a {spec.scope_param!r} parameter naming which one "
                "it is on: a fault that applies to every part is a line-wide drift, "
                "which is a different fault with a different diagnosis"
            )
        if self.params.get(RAMP_SECONDS, 0.0) < 0.0:
            raise ValueError(
                f"{self.kind} ramps over {self.params[RAMP_SECONDS]!r} s; a ramp is a "
                "duration"
            )
        if spec.form is _Form.SCALE and self.params[spec.magnitude] < 0.0:
            raise ValueError(
                f"{self.kind} scales by {self.params[spec.magnitude]!r}: a negative "
                "factor inverts the quantity rather than moving it"
            )
        if spec.scope_param is not None and spec.scope_param in self.params:
            _as_index(self.params[spec.scope_param], spec.scope_param, self.kind)


class FaultSet:
    """The faults a run carries, and the one way the plant asks about them.

    Immutable. `origin` is the instant offsets are measured from -- the line's
    `history_start`, so that a scenario fires at the same point of the simulated
    timeline whatever wall clock the run happens on.

    Raises ValueError if faults are given with no origin to place them on.
    """

    def __init__(
        self, faults: Sequence[Fault] = (), origin: datetime | None = None
    ) -> None:
        if faults and origin is None:
            raise ValueError(
                f"{len(faults)} faults were given with no origin: their offsets name "
                "no instant, so none of them could ever fire"
            )
        self._faults = tuple(faults)
        self._origin = origin

    @property
    def faults(self) -> tuple[Fault, ...]:
        """In declaration order, which is the order `modify` composes them in."""
        return self._faults

    def active_at(self, at: datetime) -> tuple[Fault, ...]:
        """Every fault whose window contains `at`, in declaration order.

        Half-open: a fault is active from its offset up to but not including its
        repair, so a scenario can hand one fault off to the next at one instant without
        the two overlapping for it.
        """
        return tuple(
            fault for fault in self._faults if self._elapsed(fault, at) is not None
        )

    def modify(
        self, quantity: str, value: float, at: datetime, **context: object
    ) -> float:
        """`value`, as every fault active at `at` and in scope for `context` leaves it.

        Returns `value` unchanged when nothing applies, which is the identity property
        this module exists for.

        Faults compose: each takes the previous one's output, so two drifts on one
        quantity both land instead of the second replacing the first. Declaration order
        decides the result wherever a scale and an offset meet on the same quantity;
        two scales, or two offsets, commute.

        Raises ValueError for a quantity no fault modifies, and for a context missing a
        key `quantity` requires (see `_CONTEXT_KEYS`). Raises TypeError for a carrier id
        or lane number that is not an integer, which a scoped fault could never match.
        """
        required = _CONTEXT_KEYS.get(quantity)
        if required is None:
            raise ValueError(
                f"no fault modifies {quantity!r}; §3.5's six modify {list(QUANTITIES)}"
            )
        missing = [key for key in required if key not in context]
        if missing:
            raise ValueError(
                f"{quantity!r} is modified per {list(required)} and this call supplied "
                f"{sorted(context)}: without {missing} a fault scoped to one carrier or "
                "one lane cannot tell whether it applies to this part"
            )

        for fault in self._faults:
            spec = _SPECS[fault.kind]
            if spec.quantity != quantity:
                continue
            elapsed = self._elapsed(fault, at)
            if elapsed is None or not _in_scope(fault, spec, context):
                continue
            value = _apply(spec, fault, elapsed, value)
        return value

    def _elapsed(self, fault: Fault, at: datetime) -> float | None:
        """Seconds since this fault fired, or None if its window does not contain
        `at`."""
        if self._origin is None:
            return None
        since = at - self._origin
        if since < fault.at or (fault.until is not None and since >= fault.until):
            return None
        return (since - fault.at).total_seconds()


NO_FAULTS: Final = FaultSet()
"""A run with nothing injected. Shared because `FaultSet` is immutable, and the default
every station carries -- so a plant built without a scenario is one that cannot be told
apart from one built with a scenario that has not fired."""


def _in_scope(fault: Fault, spec: _KindSpec, context: Mapping[str, object]) -> bool:
    if spec.defect_classes and context[DEFECT_CLASS] not in spec.defect_classes:
        return False
    if spec.scope_param is None:
        return True
    scope = fault.params.get(spec.scope_param)
    if scope is None:
        # Only reachable for a kind whose scope is optional -- §3.5's feeder starvation,
        # which may take out one lane or the whole feed.
        return True
    assert spec.scope_context is not None
    return _as_index(scope, spec.scope_param, fault.kind) == _context_index(
        context, spec.scope_context
    )


def _apply(spec: _KindSpec, fault: Fault, elapsed: float, value: float) -> float:
    ramp = fault.params.get(RAMP_SECONDS, 0.0)
    progress = 1.0 if ramp <= 0.0 else min(1.0, elapsed / ramp)
    magnitude = fault.params[spec.magnitude]
    if spec.form is _Form.SCALE:
        return value * (1.0 + (magnitude - 1.0) * progress)
    return value + magnitude * progress


def _as_index(scope: float, param: str, kind: FaultKind) -> int:
    """A carrier id or lane number, out of the float `params` can carry.

    `params` is `Mapping[str, float]` because it is written to the ground-truth log as
    JSON, where there is one number type. Refusing a non-integral value here is what
    stops `carrier=7.5` naming a carrier that does not exist and matching nothing --
    a fault that fires, is logged, and has no consequence at all.
    """
    index = int(scope)
    if index != scope:
        raise ValueError(f"{kind} names {param}={scope!r}, which is not a whole number")
    return index


def _context_index(context: Mapping[str, object], key: str) -> int:
    value = context[key]
    # `bool` is an `int` subclass, and `True == 1` would silently match carrier 1.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(
            f"context {key}={value!r} is not a carrier id or lane number, so a fault "
            f"scoped by {key} could never match it"
        )
    return value
