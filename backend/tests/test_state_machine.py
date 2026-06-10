"""Tests for grid/state_machine.py."""

import pytest

from bob.grid.state_machine import BotState, BotStateMachine, InvalidTransitionError


class TestBotStateMachine:
    def test_initial_state(self):
        sm = BotStateMachine()
        assert sm.state == BotState.IDLE

    def test_custom_initial(self):
        sm = BotStateMachine(initial=BotState.RUNNING)
        assert sm.state == BotState.RUNNING

    def test_happy_path_lifecycle(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        sm.transition(BotState.PAUSED)
        sm.transition(BotState.RUNNING)
        sm.transition(BotState.STOPPING)
        sm.transition(BotState.STOPPED)
        assert sm.state == BotState.STOPPED

    def test_history(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        assert sm.history == [BotState.IDLE, BotState.STARTING, BotState.RUNNING]

    def test_invalid_transition(self):
        sm = BotStateMachine()
        with pytest.raises(InvalidTransitionError, match="idle.*running"):
            sm.transition(BotState.RUNNING)

    def test_cannot_skip_starting(self):
        sm = BotStateMachine()
        assert not sm.can_transition(BotState.RUNNING)
        assert sm.can_transition(BotState.STARTING)

    def test_error_from_running(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        sm.transition(BotState.ERROR)
        assert sm.state == BotState.ERROR
        assert sm.is_terminal

    def test_error_from_starting(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.ERROR)
        assert sm.state == BotState.ERROR

    def test_reset_from_stopped(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        sm.transition(BotState.STOPPING)
        sm.transition(BotState.STOPPED)
        sm.reset()
        assert sm.state == BotState.IDLE

    def test_reset_from_error(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.ERROR)
        sm.reset()
        assert sm.state == BotState.IDLE

    def test_reset_from_running_fails(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        with pytest.raises(InvalidTransitionError, match="reset"):
            sm.reset()

    def test_is_active(self):
        sm = BotStateMachine()
        assert not sm.is_active
        sm.transition(BotState.STARTING)
        assert sm.is_active
        sm.transition(BotState.RUNNING)
        assert sm.is_active
        sm.transition(BotState.PAUSED)
        assert not sm.is_active

    def test_is_terminal(self):
        sm = BotStateMachine()
        assert not sm.is_terminal
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        sm.transition(BotState.STOPPING)
        sm.transition(BotState.STOPPED)
        assert sm.is_terminal

    def test_paused_to_stopping(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        sm.transition(BotState.PAUSED)
        sm.transition(BotState.STOPPING)
        assert sm.state == BotState.STOPPING

    def test_paused_to_error(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        sm.transition(BotState.PAUSED)
        sm.transition(BotState.ERROR)
        assert sm.state == BotState.ERROR

    def test_stopped_cannot_go_running(self):
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.RUNNING)
        sm.transition(BotState.STOPPING)
        sm.transition(BotState.STOPPED)
        assert not sm.can_transition(BotState.RUNNING)

    def test_starting_to_stopping(self):
        """Can abort during startup."""
        sm = BotStateMachine()
        sm.transition(BotState.STARTING)
        sm.transition(BotState.STOPPING)
        assert sm.state == BotState.STOPPING
