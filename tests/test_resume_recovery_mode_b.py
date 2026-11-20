"""R27 in Mode B: a failed resume is only restarted fresh by the reader that
still owns the session, and that is decided under the project lock.

The Mode A half is tests/test_agent_routes_medlow.py
(`test_mode_a_recovery_rechecks_owner_after_context_build`). Here the Mode B
reader (`_read_agent_stream_b`) runs to its `finish`, and the session changes
hands at one of the two points where nothing holds the project lock:

  recovery-lock   after the slow context build, as the recovery takes the lock.
                  A check made before the lock would not see the change.
  reader-release  as the reader lets go of the lock, before the recovery has
                  started. Only the owner the reader recorded while it held the
                  lock can tell this turn from the one that replaced it.

No process is started: `_launch_transaction.claim` is the first step of the
launch and the test stops there.

Run:  .venv/Scripts/python.exe -m pytest tests/test_resume_recovery_mode_b.py -q
"""
from threading import RLock
from types import SimpleNamespace

import pytest


class _TracedLock:
    """The project lock, reporting its outermost acquire and release."""

    def __init__(self):
        self._lock = RLock()
        self._depth = 0
        self.on_acquire = None
        self.on_release = None

    def _is_owned(self):
        return getattr(self._lock, '_is_owned')()

    def __enter__(self):
        self._lock.acquire()
        self._depth += 1
        if self._depth == 1 and self.on_acquire is not None:
            hook, self.on_acquire = self.on_acquire, None
            hook()
        return self

    def __exit__(self, *exc):
        self._depth -= 1
        outermost = self._depth == 0
        self._lock.release()
        if outermost and self.on_release is not None:
            hook, self.on_release = self.on_release, None
            hook()


@pytest.fixture
def mode_b(tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as routes

    proc = SimpleNamespace(pid=123, stdout=(), wait=lambda: 1)
    session = {
        'session_id': 'child', 'project_id': 'sample', 'status': 'running', 'mode': 'B',
        'proc': proc, 'incognito': True, 'log_lines': [], 'task': 'test',
        '_delivery_attempt_id': 'old-turn', '_resume_id': 'prior',
        '_dispatch_time': routes._time.time(),
    }
    lock = _TracedLock()
    env = SimpleNamespace(routes=routes, proc=proc, session=session, lock=lock,
                          built=[], claims=[], in_finish=[])

    def claim(candidate):
        assert lock._is_owned()
        env.claims.append(candidate)
        raise RuntimeError('stop test before launch')

    monkeypatch.setattr(routes, 'agent_sessions', {'child': session})
    monkeypatch.setattr(routes, 'get_manager', lambda _: SimpleNamespace(lock=lock))
    monkeypatch.setattr(routes, 'load_project', lambda _: {'project_path': str(tmp_path)})
    monkeypatch.setattr(routes, '_unregister_process', lambda _: None)
    monkeypatch.setattr(routes._credits_session, 'replay_after_exit', lambda *a: False)
    monkeypatch.setattr(routes, '_log_agent_completion', lambda _: env.in_finish.append(lock._is_owned()))
    monkeypatch.setattr(routes, '_run_runtime_callbacks', lambda _: None)
    monkeypatch.setattr(routes, '_log_agent_activity', lambda *a: None)
    monkeypatch.setattr(routes._launch_transaction, 'claim', claim)
    return env


_CHANGES = {
    'process': lambda s: s.__setitem__('proc', object()),
    'turn': lambda s: s.__setitem__('_delivery_attempt_id', 'new-turn'),
    'resume': lambda s: s.__setitem__('_resume_id', 'another'),
    'status': lambda s: s.__setitem__('status', 'running'),
}


def _assert_no_recovery(env):
    assert env.in_finish == [True]            # the reader did finish as the owner
    assert env.built == ['context']           # ...and the recovery got as far as the check
    assert env.claims == []
    assert not env.session.get('_resume_recovery_attempted')
    assert not any('restarting fresh' in str(line) for line in env.session['log_lines'])


def test_mode_b_recovery_launches_when_nothing_changed(mode_b, monkeypatch):
    """Control: the harness does reach the launch when the session is unchanged."""
    env = mode_b
    monkeypatch.setattr(env.routes, '_fresh_context_for',
                        lambda *a: env.built.append('context') or 'context')
    env.routes._read_agent_stream_b(env.proc, env.session)
    assert env.claims == [env.session]
    assert env.session['_resume_recovery_attempted']


@pytest.mark.parametrize('change', sorted(_CHANGES))
def test_mode_b_recovery_rechecks_the_session_under_the_lock(mode_b, monkeypatch, change):
    env = mode_b

    def build(*args):
        env.built.append('context')
        # The change lands while the recovery is taking the project lock.
        env.lock.on_acquire = lambda: _CHANGES[change](env.session)
        return 'context'

    monkeypatch.setattr(env.routes, '_fresh_context_for', build)
    env.routes._read_agent_stream_b(env.proc, env.session)
    _assert_no_recovery(env)


def test_mode_b_recovery_keeps_the_owner_the_reader_saw_under_its_lock(mode_b, monkeypatch):
    env = mode_b
    monkeypatch.setattr(env.routes, '_fresh_context_for',
                        lambda *a: env.built.append('context') or 'context')

    def completion(_session):
        env.in_finish.append(env.lock._is_owned())
        # A new turn is delivered to the same process the moment the reader
        # releases the lock: same proc, new delivery attempt.
        env.lock.on_release = lambda: _CHANGES['turn'](env.session)

    monkeypatch.setattr(env.routes, '_log_agent_completion', completion)
    env.routes._read_agent_stream_b(env.proc, env.session)
    assert env.session['_delivery_attempt_id'] == 'new-turn'
    _assert_no_recovery(env)
