"""Immutable ownership carried from prompt publication to failure persistence."""
from dataclasses import dataclass
from typing import Any, TypeVar


_Error = TypeVar('_Error', bound=Exception)


@dataclass(frozen=True)
class _Owner:
    session: dict[str, Any] | None
    proc: Any
    turn: int

    @classmethod
    def capture(cls, session: dict[str, Any] | None, proc: Any = None) -> '_Owner':
        if proc is None and session is not None:
            proc = session.get('proc')
        return cls(session, proc,
                   int(session.get('_delegation_turn', 1)) if session is not None else 1)

    def matches(self, current: dict[str, Any] | None, *, disowned: bool = False) -> bool:
        if current is not self.session:
            return False
        if current is None:
            return True
        return (int(current.get('_delegation_turn', 1)) == self.turn
                and (current.get('proc') is self.proc
                     or (disowned and current.get('proc') is None)))


@dataclass(frozen=True)
class DeliveryAttempt:
    owner: _Owner
    restored_owner: _Owner

    @classmethod
    def capture(cls, session: dict[str, Any] | None,
                prior_session: dict[str, Any] | None = None, *, proc: Any = None) -> 'DeliveryAttempt':
        # Capture before the acknowledgment can wait or rollback can disown it.
        return cls(_Owner.capture(session, proc), _Owner.capture(prior_session))

    @property
    def turn(self) -> int:
        return self.owner.turn

    def owns(self, current: dict[str, Any] | None) -> bool:
        return (self.owner.matches(current, disowned=True)
                or self.restored_owner.matches(current))

    def bind(self, error: _Error) -> _Error:
        setattr(error, 'delivery_attempt', self)
        return error
