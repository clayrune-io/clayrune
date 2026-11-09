"""One durable owner across reservation, provider launch and publication.

The spawn factory promises that an unmarked OSError is a pre-child refusal.
It marks every exception from an uncertain or post-child branch ambiguous.
No failure in this module retries a launch.
"""
from copy import deepcopy
from functools import wraps
from typing import Any, Callable, ParamSpec, TypeVar

from mc.delivery_attempt import DeliveryAttempt


_TURN_KEYS = (
    '_delivery_attempt_id', '_delegation_turn', '_delivery_generation',
    '_turn_owner', '_notify_session', '_notify_session_sent',
    '_empty_turn', '_turn_tool_calls', '_limit_resume_completion',
    '_limit_resume_exhaustion', '_allowance_exhausted', '_limit_resume_attempt',
    '_limit_resume_cancelled',
)


class ReservationLost(RuntimeError):
    """No launch took place, but another owner prevents restoration or fallback."""


class LaunchTransaction:
    """Created before the reservation; never reconstructed from a later row."""

    def __init__(self, session: dict[str, Any]):
        self.session = session
        self.project_id = session.get('project_id') or ''
        self.session_id = session.get('session_id') or ''
        self.history = bool(self.project_id and self.session_id
                            and not session.get('incognito')
                            and not session.get('housekeeping'))
        self.before: dict[str, Any] = {}
        self.prior_row: dict[str, Any] | None = None
        self.attempt = DeliveryAttempt('')
        self.launched = False
        self.restored = False
        self.destination = False

    def reserve(self, action: Callable[[], Any], *, running: bool = False) -> Any:
        from mc.blueprints import agent_routes as ar
        with ar.get_manager(self.project_id).lock:
            if self.session.get('_launch_uncertain'):
                raise RuntimeError('previous launch outcome requires review; not retried')
            self.before = {k: deepcopy(self.session[k]) for k in _TURN_KEYS if k in self.session}
            if self.history:
                row = next((r for r in ar._load_agent_log(self.project_id)
                            if r.get('session_id') == self.session_id), None)
                self.prior_row = deepcopy(row)
                if row and row.get('launch_outcome') == 'unknown':
                    raise RuntimeError('previous durable launch outcome requires review; not retried')
            try:
                result = action()
            except BaseException:
                if running:
                    # Execution already exists. Keep the rearmed callback and
                    # allocated turn; only its failed owner replacement rolls
                    # back. Restoring a spent latch would lose the callback.
                    if '_delivery_attempt_id' in self.before:
                        self.session['_delivery_attempt_id'] = self.before['_delivery_attempt_id']
                    else:
                        self.session.pop('_delivery_attempt_id', None)
                else:
                    self._restore_live()
                raise
            self.attempt = DeliveryAttempt(self.session.get('_delivery_attempt_id') or '')
            if self.history:
                self.destination = any(r.get('session_id') == self.session_id
                                       for r in ar._load_agent_log(self.project_id))
            self.launched = running
            return result

    def _restore_live(self) -> None:
        for key in _TURN_KEYS:
            if key in self.before:
                self.session[key] = deepcopy(self.before[key])
            else:
                self.session.pop(key, None)

    def _owns_live(self) -> bool:
        return self.session.get('_delivery_attempt_id', '') == self.attempt.attempt_id

    def restore(self) -> bool:
        """CAS both live and durable owners before restoring a proven refusal."""
        from mc.blueprints import agent_routes as ar
        with ar.get_manager(self.project_id).lock:
            if self.launched or not self._owns_live():
                return False
            matched = not self.history

            def undo(rows):
                nonlocal matched
                index = next((i for i, r in enumerate(rows)
                              if r.get('session_id') == self.session_id), None)
                if index is None or not self.attempt.owns_row(rows[index]):
                    return
                matched = True
                if self.prior_row is None:
                    rows.pop(index)
                else:
                    rows[index] = deepcopy(self.prior_row)

            if self.history:
                ar._update_agent_log(self.project_id, undo)
            if matched:
                self._restore_live()
                self.restored = True
            return matched

    def _unknown(self) -> None:
        from mc.blueprints import agent_routes as ar
        with ar.get_manager(self.project_id).lock:
            if not self._owns_live():
                return
            self.session['_launch_uncertain'] = True
            self.session['_stdin_write_uncertain'] = True
            # Prevent guardian replay through pending_recovery_message.
            self.session['pending_recovery_message'] = None

            def mark(rows):
                for row in rows:
                    if row.get('session_id') == self.session_id and self.attempt.owns_row(row):
                        row['launch_outcome'] = 'unknown'

            if self.history:
                try:
                    ar._update_agent_log(self.project_id, mark)
                except Exception as exc:
                    ar._log(f'[launch-transaction] unknown-outcome save failed: {exc}')

    def launch(self, spawn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        from mc.blueprints import agent_routes as ar
        with ar.get_manager(self.project_id).lock:
            if self.launched or not self._owns_live():
                raise ReservationLost('launch reservation is no longer ours')
            if self.history:
                row = next((r for r in ar._load_agent_log(self.project_id)
                            if r.get('session_id') == self.session_id), None)
                if (self.destination or row is not None) and not self.attempt.owns_row(row or {}):
                    raise ReservationLost('durable launch reservation was superseded')
        try:
            proc = spawn(*args, **kwargs)
        except BaseException as exc:
            if isinstance(exc, OSError) and not getattr(exc, '_mc_launch_ambiguous', False):
                try:
                    self.restore()
                except Exception as restore_error:
                    ar._log(f'[launch-transaction] refusal restoration failed: {restore_error}')
                    self._unknown()
            else:
                setattr(exc, '_mc_launch_ambiguous', True)
                self.launched = True
                self._unknown()
            raise
        self.launched = True
        return proc

    def publish(self, session: dict[str, Any]) -> None:
        """Carry the reserved token, including after a post-child save failure."""
        if self.attempt.attempt_id:
            self.attempt.stamp(session)

    def settle(self, session: dict[str, Any], **kwargs: Any) -> Any:
        from mc.blueprints import agent_routes as ar
        self.publish(session)
        return ar._log_agent_dispatch_pending(
            session, attempt=self.attempt if self.attempt.attempt_id else None,
            postlaunch=True, **kwargs)


def reserve(session: dict[str, Any], action: Callable[[], Any], *,
            running: bool = False) -> LaunchTransaction:
    transaction = LaunchTransaction(session)
    transaction.reserve(action, running=running)
    return transaction


def claim(session: dict[str, Any]) -> LaunchTransaction:
    """Recovery/revival keep history intact while taking its delivery owner."""
    transaction = LaunchTransaction(session)

    def take():
        prior_owner = session.get('_delivery_attempt_id')
        if (prior_owner and transaction.prior_row is not None
                and not DeliveryAttempt(prior_owner).owns_row(transaction.prior_row)):
            raise ReservationLost('recovery no longer owns its durable destination')
        return DeliveryAttempt.capture(session)

    transaction.reserve(take)
    return transaction


def launch(transaction: LaunchTransaction | None, spawn: Callable[..., Any],
           *args: Any, **kwargs: Any) -> Any:
    if transaction is None:
        raise RuntimeError('launch requires a prelaunch transaction')
    return transaction.launch(spawn, *args, **kwargs)


def start_turn(session: dict[str, Any], owner: str | None, *,
               running: bool = False) -> LaunchTransaction:
    from mc.blueprints import agent_routes as ar

    def advance():
        turn_owner = owner
        internal = turn_owner is None or bool(
            ar.has_request_context() and ar.request.environ.get('clayrune.limit_resume'))
        ar._limit_resume.new_turn(session, internal=internal)
        if internal:
            turn_owner = None
        if turn_owner:
            session[ar._TURN_OWNER_KEY] = turn_owner
        ar._empty_turn.begin_turn(session)
        ar._allocate_delegation_turn(session)
        ar._log_agent_dispatch_pending(session, strict=True, write_turn_start=False,
                                       postlaunch=running)

    return reserve(session, advance, running=running)


def rearm(session: dict[str, Any], advance: Callable[[], Any], *,
          running: bool = False) -> LaunchTransaction:
    def prepare():
        session.pop('_notify_session_sent', None)
        return advance()
    return reserve(session, prepare, running=running)


_P = ParamSpec('_P')
_T = TypeVar('_T')


def spawn_boundary(factory: Callable[_P, _T]) -> Callable[_P, _T]:
    """Only the exact Popen refusal carries proof that no child was created.

    Preserve exception type/traceback for existing callers. All other errors,
    including OSError from delivery or shim setup, forbid transaction rollback.
    """
    @wraps(factory)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        try:
            return factory(*args, **kwargs)
        except BaseException as exc:
            if not getattr(exc, '_mc_prechild_refusal', False):
                setattr(exc, '_mc_launch_ambiguous', True)
            raise
    return wrapped
