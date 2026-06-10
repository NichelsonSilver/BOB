"""Bot state machine with validated transitions.

No I/O — pure state logic.
"""

from enum import Enum


class BotState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# Valid transitions: from_state -> set of allowed to_states
_TRANSITIONS: dict[BotState, set[BotState]] = {
    BotState.IDLE: {BotState.STARTING},
    BotState.STARTING: {BotState.RUNNING, BotState.ERROR, BotState.STOPPING},
    BotState.RUNNING: {BotState.PAUSED, BotState.STOPPING, BotState.ERROR},
    BotState.PAUSED: {BotState.RUNNING, BotState.STOPPING, BotState.ERROR},
    BotState.STOPPING: {BotState.STOPPED, BotState.ERROR},
    BotState.STOPPED: {BotState.IDLE},
    BotState.ERROR: {BotState.IDLE},
}


class InvalidTransitionError(Exception):
    pass


class BotStateMachine:
    """Tracks bot state and validates transitions."""

    def __init__(self, initial: BotState = BotState.IDLE) -> None:
        self._state = initial
        self._history: list[BotState] = [initial]

    @property
    def state(self) -> BotState:
        return self._state

    @property
    def history(self) -> list[BotState]:
        return list(self._history)

    def can_transition(self, to: BotState) -> bool:
        return to in _TRANSITIONS.get(self._state, set())

    def transition(self, to: BotState) -> BotState:
        """Transition to a new state.

        Raises InvalidTransitionError if the transition is not allowed.
        """
        if not self.can_transition(to):
            raise InvalidTransitionError(
                f"Cannot transition from {self._state.value} to {to.value}. "
                f"Allowed: {sorted(s.value for s in _TRANSITIONS.get(self._state, set()))}"
            )
        self._state = to
        self._history.append(to)
        return self._state

    @property
    def is_active(self) -> bool:
        """True if the bot is in a state where it should be processing."""
        return self._state in (BotState.RUNNING, BotState.STARTING)

    @property
    def is_terminal(self) -> bool:
        """True if the bot is stopped or errored."""
        return self._state in (BotState.STOPPED, BotState.ERROR)

    def reset(self) -> None:
        """Reset to IDLE (only from terminal states)."""
        if self._state in (BotState.STOPPED, BotState.ERROR):
            self.transition(BotState.IDLE)
        else:
            raise InvalidTransitionError(
                f"Can only reset from STOPPED or ERROR, currently {self._state.value}"
            )
