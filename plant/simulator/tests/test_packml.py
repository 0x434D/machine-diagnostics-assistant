"""§3.3's state machine. These assert the *semantics* -- which states are reachable
from which, and that a Suspended transition cannot exist without naming its buffer --
never the shape of the transition table, which is free to change."""

from __future__ import annotations

import pytest
from simulator.packml import Command, State, StateMachine, SuspendReason


def running() -> StateMachine:
    """A machine in Execute, reached the way a real one reaches it."""
    machine = StateMachine()
    machine.apply(Command.CLEAR)
    machine.settle()
    machine.apply(Command.RESET)
    machine.settle()
    machine.apply(Command.START)
    machine.settle()
    return machine


def test_there_are_exactly_fifteen_states_and_batch_operation_is_not_among_them() -> (
    None
):
    """§3.3: the subset that applies to a continuously running line. Completing and
    Complete belong to batch operation and their absence is the claim being made."""
    assert len(State) == 15
    assert not {"Completing", "Complete"} & {state.value for state in State}


def test_a_cold_machine_reaches_execute_through_the_states_isa_requires() -> None:
    machine = StateMachine()
    # `machine.state` is bound to `state` before each identity assert rather than
    # compared inline: mypy narrows a read-only property's `is` comparison and does
    # not invalidate that narrowing across the `apply()` call that actually changes
    # it, so an inline `assert machine.state is State.CLEARING` after `apply()` was
    # rejected as an always-false "Non-overlapping identity check" against the
    # `State.ABORTED` narrowed two lines above -- confirmed by isolating the pattern
    # outside this file. A fresh local assignment sidesteps it without weakening what
    # is asserted.
    state: State = machine.state
    assert state is State.ABORTED

    machine.apply(Command.CLEAR)
    state = machine.state
    assert state is State.CLEARING
    assert machine.settle() is State.STOPPED

    machine.apply(Command.RESET)
    state = machine.state
    assert state is State.RESETTING
    assert machine.settle() is State.IDLE

    machine.apply(Command.START)
    state = machine.state
    assert state is State.STARTING
    assert machine.settle() is State.EXECUTE


def test_suspending_without_naming_a_buffer_is_refused() -> None:
    """§3.3: 'Every Suspended transition records which buffer and which direction.
    That field is what makes propagation verifiable rather than inferred, so it is
    not optional.' A machine that can enter Suspended with an empty reason makes the
    whole propagation claim unverifiable."""
    machine = running()
    with pytest.raises(ValueError, match="reason"):
        machine.apply(Command.SUSPEND)


def test_a_suspend_reason_that_is_merely_a_string_is_still_refused() -> None:
    """Hold's reason is a free-form str ("jam"), so a bare `reason is not None`
    check would let a string that merely *looks* like `direction:buffer_id` through
    Suspend too -- indistinguishable from a real SuspendReason until Task 8's
    gateway tries to resolve the buffer it names and finds nothing there."""
    machine = running()
    with pytest.raises(ValueError, match="reason"):
        machine.apply(Command.SUSPEND, "starved:B2_3")


def test_a_suspended_station_names_its_buffer_and_its_direction() -> None:
    machine = running()
    machine.apply(Command.SUSPEND, SuspendReason("starved", "B2_3"))
    machine.settle()
    assert machine.state is State.SUSPENDED
    assert machine.reason == "starved:B2_3"


def test_suspended_clears_itself_and_held_does_not() -> None:
    """The distinction the whole diagnostic model rests on: Suspended is a
    consequence and resolves when the buffer does; Held is a cause candidate and
    waits for an operator."""
    suspended = running()
    suspended.apply(Command.SUSPEND, SuspendReason("blocked", "B3_4"))
    suspended.settle()
    assert suspended.clears_itself is True

    held = running()
    held.apply(Command.HOLD, "jam")
    held.settle()
    assert held.state is State.HELD
    assert held.clears_itself is False


def test_leaving_suspended_clears_the_reason() -> None:
    """A stale reason on a running station would read as a station still waiting."""
    machine = running()
    machine.apply(Command.SUSPEND, SuspendReason("starved", "B1_2"))
    machine.settle()
    machine.apply(Command.UNSUSPEND)
    assert machine.settle() is State.EXECUTE
    assert machine.reason == ""


def test_aborting_is_reachable_from_every_state() -> None:
    """A fault shutdown does not wait for the machine to be in a convenient state."""
    for state in State:
        machine = StateMachine(state)
        machine.apply(Command.ABORT)
        assert machine.state is State.ABORTING
        assert machine.settle() is State.ABORTED


def test_stop_does_not_shortcut_the_recovery_from_aborted() -> None:
    """ISA-TR88.00.02 makes only Abort universal. Stop and Clear both land on
    Stopped, so if Stop worked from Aborted a station could skip the Clearing step
    recovery is supposed to require."""
    machine = StateMachine()  # Aborted
    with pytest.raises(ValueError, match="Stop"):
        machine.apply(Command.STOP)

    machine.apply(Command.CLEAR)
    assert machine.state is State.CLEARING


def test_a_command_the_current_state_does_not_accept_raises() -> None:
    """Silently ignoring an illegal command is how a station ends up in a state
    nothing put it in."""
    machine = StateMachine()  # Aborted
    with pytest.raises(ValueError, match="Start"):
        machine.apply(Command.START)


def test_settle_does_nothing_in_a_waiting_state() -> None:
    machine = running()
    assert machine.settle() is None
    assert machine.state is State.EXECUTE
