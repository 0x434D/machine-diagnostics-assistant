"""PackML per ISA-TR88.00.02 (§3.3), in the subset that applies to a continuously
running line.

We adopt PackML *semantics and structure*, not the OPC UA companion specification
(OPC 30050). An automation engineer recognises the model immediately; this project
does not claim conformance, and §3.3 says so openly.

Fifteen states: `Completing` and `Complete` are omitted because they describe a batch
finishing, and this line never finishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class State(str, Enum):
    # Waiting states -- the machine sits here until commanded.
    ABORTED = "Aborted"
    STOPPED = "Stopped"
    IDLE = "Idle"
    EXECUTE = "Execute"
    HELD = "Held"
    SUSPENDED = "Suspended"
    # Acting states -- the machine passes through, and settle() advances it.
    ABORTING = "Aborting"
    CLEARING = "Clearing"
    STOPPING = "Stopping"
    RESETTING = "Resetting"
    STARTING = "Starting"
    HOLDING = "Holding"
    UNHOLDING = "Unholding"
    SUSPENDING = "Suspending"
    UNSUSPENDING = "Unsuspending"


class Command(str, Enum):
    ABORT = "Abort"
    CLEAR = "Clear"
    STOP = "Stop"
    RESET = "Reset"
    START = "Start"
    HOLD = "Hold"
    UNHOLD = "Unhold"
    SUSPEND = "Suspend"
    UNSUSPEND = "Unsuspend"


@dataclass(frozen=True)
class SuspendReason:
    """§3.3: which buffer, and which direction. Both, always.

    `starved` means the upstream buffer is empty; `blocked` means the downstream one
    is full. Rendering as `direction:buffer_id` keeps the OPC UA `StateReason` node a
    plain string while still carrying two fields, which is what §4.1's address space
    declares it to be.
    """

    direction: Literal["starved", "blocked"]
    buffer_id: str

    def __str__(self) -> str:
        return f"{self.direction}:{self.buffer_id}"


_SETTLES_TO: dict[State, State] = {
    State.ABORTING: State.ABORTED,
    State.CLEARING: State.STOPPED,
    State.STOPPING: State.STOPPED,
    State.RESETTING: State.IDLE,
    State.STARTING: State.EXECUTE,
    State.HOLDING: State.HELD,
    State.UNHOLDING: State.EXECUTE,
    State.SUSPENDING: State.SUSPENDED,
    State.UNSUSPENDING: State.EXECUTE,
}

_ACCEPTS: dict[Command, frozenset[State]] = {
    Command.CLEAR: frozenset({State.ABORTED}),
    Command.RESET: frozenset({State.STOPPED}),
    Command.START: frozenset({State.IDLE}),
    Command.HOLD: frozenset({State.EXECUTE}),
    Command.UNHOLD: frozenset({State.HELD}),
    Command.SUSPEND: frozenset({State.EXECUTE}),
    Command.UNSUSPEND: frozenset({State.SUSPENDED}),
    # Stop is accepted only from the operating states (ISA-TR88.00.02): a station
    # that is Aborted, Aborting, Clearing, Stopping or already Stopped must recover
    # through Clearing then Reset, not through Stop -- Stop and Clear both land on
    # Stopped, so letting Stop fire from Aborted would erase the distinction M3's
    # diagnosis needs between "stopped through the required recovery path" and
    # "stopped from a fault shutdown that never went through it." Abort has no such
    # restriction: ISA makes only Abort universal, which is exactly what an
    # unconditional fault shutdown has to be.
    Command.STOP: frozenset(
        {
            State.IDLE,
            State.STARTING,
            State.EXECUTE,
            State.HOLDING,
            State.HELD,
            State.UNHOLDING,
            State.SUSPENDING,
            State.SUSPENDED,
            State.UNSUSPENDING,
            State.RESETTING,
        }
    ),
    Command.ABORT: frozenset(State),
}

_ENTERS: dict[Command, State] = {
    Command.CLEAR: State.CLEARING,
    Command.RESET: State.RESETTING,
    Command.START: State.STARTING,
    Command.HOLD: State.HOLDING,
    Command.UNHOLD: State.UNHOLDING,
    Command.SUSPEND: State.SUSPENDING,
    Command.UNSUSPEND: State.UNSUSPENDING,
    Command.STOP: State.STOPPING,
    Command.ABORT: State.ABORTING,
}

_CARRIES_REASON = frozenset(
    {State.SUSPENDING, State.SUSPENDED, State.HOLDING, State.HELD}
)


class StateMachine:
    """One station's PackML state. Pure logic: no clock, no I/O, no address space.

    Starts in `Aborted` because that is where a machine that has never been cleared
    genuinely is -- starting in `Execute` would mean every station's history begins
    with a transition nothing caused.
    """

    def __init__(self, state: State = State.ABORTED) -> None:
        self._state = state
        self._reason = ""

    @property
    def state(self) -> State:
        return self._state

    @property
    def reason(self) -> str:
        """The `StateReason` node's value: empty unless the state carries one."""
        return self._reason

    @property
    def clears_itself(self) -> bool:
        """§3.3's cause/consequence split, as a property rather than a comment.

        `Suspended` is a consequence by definition and resolves when the buffer that
        caused it does. `Held` and `Aborted` are cause candidates and need an
        operator. M3's analysis turns on exactly this distinction, and the plant is
        where it is decided.
        """
        return self._state in (State.SUSPENDED, State.SUSPENDING)

    def apply(
        self, command: Command, reason: SuspendReason | str | None = None
    ) -> State:
        """Raises ValueError if the current state does not accept `command`, if a
        reason-carrying transition was given none, or if Suspend's reason is not a
        SuspendReason."""
        if self._state not in _ACCEPTS[command]:
            raise ValueError(f"{command.value} is not accepted in {self._state.value}")

        entering = _ENTERS[command]
        if command is Command.SUSPEND:
            # Suspend is checked by *type*, not merely presence. Hold shares
            # _CARRIES_REASON with Suspend but legitimately takes a free-form str
            # ("jam"); if Suspend accepted one too, a string that merely looks right
            # -- "starved:B2_3" -- would pass a bare `reason is not None` check while
            # carrying no structure Task 8's gateway can parse. It would be stored
            # indistinguishably from a real SuspendReason until something downstream
            # tried to resolve the buffer and found nothing there.
            if not isinstance(reason, SuspendReason):
                raise ValueError(
                    f"{command.value} needs a reason: §3.3 makes the field that names "
                    "which buffer and which direction non-optional, because it is what "
                    "makes propagation verifiable rather than inferred. Got "
                    f"{reason!r} instead of a SuspendReason."
                )
            self._reason = str(reason)
        elif entering in _CARRIES_REASON:
            if reason is None:
                raise ValueError(
                    f"{command.value} needs a reason: §3.3 makes the field that names "
                    "which buffer and which direction non-optional, because it is what "
                    "makes propagation verifiable rather than inferred"
                )
            self._reason = str(reason)
        else:
            self._reason = ""

        self._state = entering
        return self._state

    def settle(self) -> State | None:
        """Advance an acting state to the waiting state it leads to.

        Returns the new state, or None if the machine was already waiting. Acting
        states are instantaneous here -- a station that took measurable wall time to
        start would be modelling motor spin-up, which no scenario in §3.5 turns on.
        """
        settled = _SETTLES_TO.get(self._state)
        if settled is None:
            return None
        self._state = settled
        if settled not in _CARRIES_REASON:
            self._reason = ""
        return settled
