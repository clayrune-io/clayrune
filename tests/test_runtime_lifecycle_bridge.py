"""Focused tests for the injected, provider-neutral runtime bridge seam."""
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from mc.agent_runtime import SessionHandle
from mc.runtime_attempt_owner import DispatchFacts


class FakeRuntime:
    name = 'codex'

    def __init__(self, events):
        self.events = events
        self.calls = []
        self.callbacks = None

    def model_supported(self, model):
        return True

    def build_command(self, **kwargs):
        return ['fake']

    def transcript_path(self, project_path, native_id):
        self.events.append(('transcript', project_path, native_id))
        return Path(project_path) / 'rollout.jsonl'

    def dispatch(self, **kwargs):
        self.events.append('spawn')
        self.calls.append(kwargs)
        self.callbacks = kwargs['callbacks']
        return SessionHandle(kwargs['mc_session_id'], 'codex', 'A',
                             kwargs['project_path'], kwargs['project_id'],
                             kwargs['session_dict'], meta=kwargs['callbacks'])


@pytest.fixture()
def env(monkeypatch, tmp_path):
    import server  # noqa: F401
    from mc import state
    from mc.blueprints import agent_routes as ar
    runtime = FakeRuntime([])
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtime)
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': pid, 'project_path': str(tmp_path)})
    old = dict(state.agent_sessions)
    state.agent_sessions.clear()
    yield ar, runtime, state.agent_sessions, tmp_path
    state.agent_sessions.clear()
    state.agent_sessions.update(old)


def test_dispatch_without_bridge_uses_global_callbacks(env):
    ar, runtime, sessions, tmp = env
    before = ar._RUNTIME_CALLBACKS
    sid = ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex')
    assert sid in sessions and len(runtime.calls) == 1
    assert runtime.calls[0]['callbacks'] is before
    assert ar._RUNTIME_CALLBACKS is before


def test_dispatch_facts_are_exact_and_recursive_immutable():
    facts = DispatchFacts('p', 'C:/work', 'mc1', 'codex', '', '', 'resume', 'task', False,
                           {'nested': {'model': ''}})
    assert facts.model == '' and facts.effort == '' and facts.resume_id == 'resume'
    assert isinstance(facts.provenance, MappingProxyType)
    with pytest.raises(TypeError):
        facts.provenance['nested']['model'] = 'x'
    with pytest.raises(ValueError):
        DispatchFacts('', 'x', 'mc', 'codex', 'm', None, '', 't', False)
    with pytest.raises(ValueError):
        DispatchFacts('p', 'x', 'mc', 'codex', 'm', None, '', 't', 1)


def test_bridge_order_and_exact_facts(env):
    ar, runtime, sessions, tmp = env
    events = runtime.events
    class Bridge:
        def prepare(self, facts): events.append(('prepare', facts.model, facts.effort, facts.resume_id))
        def launch(self, spawn): events.append('launch'); return spawn()
        def on_init(self, event, session): events.append('init')
        def on_exit(self, event, session, source): events.append(('exit', source))
    bridges = []
    sid = ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
        model_override='', effort_override='', resume_id='native',
        lifecycle_bridge_factory=lambda facts: (bridges.append(facts) or Bridge()))
    assert sid in sessions
    assert [x[0] if isinstance(x, tuple) else x for x in events[:3]] == ['prepare', 'launch', 'spawn']
    assert bridges[0].model == '' and bridges[0].resume_id == 'native'


def test_factory_or_prepare_failure_never_calls_runtime(env):
    ar, runtime, sessions, tmp = env
    for failure in ('factory', 'prepare'):
        def make(facts, failure=failure):
            if failure == 'factory': raise RuntimeError('factory')
            class B:
                def prepare(self, facts): raise RuntimeError('prepare')
            return B()
        with pytest.raises(RuntimeError):
            ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
                                      lifecycle_bridge_factory=make)
    assert not runtime.calls
    assert all(s['status'] == 'error' and not s['process_alive'] for s in sessions.values())


def test_callbacks_are_independent_and_resolve_transcript_at_exit(env):
    ar, runtime, sessions, tmp = env
    seen = []
    old_init, old_exit = ar._RUNTIME_CALLBACKS['on_init'], ar._RUNTIME_CALLBACKS['on_process_exit']
    def prior_init(e, s): seen.append('prior-init'); raise RuntimeError('old')
    def prior_exit(e, s): seen.append('prior-exit'); raise RuntimeError('old')
    ar._RUNTIME_CALLBACKS['on_init'] = prior_init
    ar._RUNTIME_CALLBACKS['on_process_exit'] = prior_exit
    class B:
        def prepare(self, f): pass
        def launch(self, spawn): return spawn()
        def on_init(self, e, s): seen.append('bridge-init')
        def on_exit(self, e, s, source): seen.append(('bridge-exit', source))
    try:
        sid = ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex', lifecycle_bridge_factory=lambda f: B())
        s = sessions[sid]; s['provider_session_id'] = 'native'
        runtime.callbacks['on_init'](SimpleNamespace(payload={'session_id': 'native'}), s)
        runtime.callbacks['on_process_exit'](None, s)
        assert seen == ['prior-init', 'bridge-init', 'prior-exit', ('bridge-exit', tmp / 'rollout.jsonl')]
    finally:
        ar._RUNTIME_CALLBACKS['on_init'], ar._RUNTIME_CALLBACKS['on_process_exit'] = old_init, old_exit


def test_two_injected_dispatches_are_isolated(env):
    ar, runtime, sessions, tmp = env
    facts = []
    class B:
        def prepare(self, f): facts.append(f)
        def launch(self, spawn): return spawn()
        def on_init(self, e, s): pass
        def on_exit(self, e, s, source): pass
    a = ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'a', provider_name='codex', lifecycle_bridge_factory=lambda f: B())
    b = ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'b', provider_name='codex', lifecycle_bridge_factory=lambda f: B())
    assert a != b and len(facts) == 2 and facts[0].mc_session_id != facts[1].mc_session_id


@pytest.mark.parametrize('launch_result', [None, object()])
def test_bad_launch_result_fails_closed_without_retry(env, launch_result):
    ar, runtime, sessions, tmp = env
    class B:
        def prepare(self, facts): pass
        def launch(self, spawn): return launch_result
        def on_init(self, e, s): pass
        def on_exit(self, e, s, source): pass
    with pytest.raises(TypeError):
        ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex', lifecycle_bridge_factory=lambda f: B())
    assert not runtime.calls
    assert all(s['status'] == 'error' for s in sessions.values())


def test_launch_exception_is_persisted_without_retry(env):
    ar, runtime, sessions, tmp = env
    class B:
        def prepare(self, facts): pass
        def launch(self, spawn): raise RuntimeError('blocked')
        def on_init(self, e, s): pass
        def on_exit(self, e, s, source): pass
    with pytest.raises(RuntimeError):
        ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex', lifecycle_bridge_factory=lambda f: B())
    assert not runtime.calls and list(sessions.values())[-1]['status'] == 'error'


def test_resolver_failure_is_visible_and_does_not_escape(env):
    ar, runtime, sessions, tmp = env
    runtime.transcript_path = lambda *a: (_ for _ in ()).throw(RuntimeError('lookup'))
    seen = []
    class B:
        def prepare(self, facts): pass
        def launch(self, spawn): return spawn()
        def on_init(self, e, s): pass
        def on_exit(self, e, s, source): seen.append(source)
    sid = ar._dispatch_via_runtime({'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex', lifecycle_bridge_factory=lambda f: B())
    s = sessions[sid]; s['provider_session_id'] = 'native'
    runtime.callbacks['on_init'](SimpleNamespace(payload={'session_id': 'native'}), s)
    assert seen == [] and s['_lifecycle_errors']


def test_invalid_incognito_is_not_coerced_before_bridge_validation(env):
    ar, runtime, sessions, tmp = env
    factory_called = False

    def factory(_facts):
        nonlocal factory_called
        factory_called = True

    with pytest.raises(ValueError, match='incognito must be boolean'):
        ar._dispatch_via_runtime(
            {'id': 'p', 'project_path': str(tmp)}, 'task',
            provider_name='codex', incognito='false',
            lifecycle_bridge_factory=factory,
        )
    assert not factory_called
    assert not runtime.calls


def test_bridge_callback_failures_are_isolated_and_globals_unchanged(env):
    ar, runtime, sessions, tmp = env
    seen = []
    global_callbacks = ar._RUNTIME_CALLBACKS
    prior_init = global_callbacks['on_init']
    prior_exit = global_callbacks['on_process_exit']

    def recording_init(event, session):
        seen.append('prior-init')

    def recording_exit(event, session):
        seen.append('prior-exit')

    global_callbacks['on_init'] = recording_init
    global_callbacks['on_process_exit'] = recording_exit

    class B:
        def prepare(self, facts): pass
        def launch(self, spawn): return spawn()
        def on_init(self, event, session): raise RuntimeError('bridge-init')
        def on_exit(self, event, session, source): raise RuntimeError('bridge-exit')

    try:
        sid = ar._dispatch_via_runtime(
            {'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
            lifecycle_bridge_factory=lambda facts: B(),
        )
        session = sessions[sid]
        session['provider_session_id'] = 'native'
        injected_callbacks = runtime.callbacks
        injected_callbacks['on_init'](
            SimpleNamespace(payload={'session_id': 'native'}), session)
        injected_callbacks['on_process_exit'](None, session)
        assert seen == ['prior-init', 'prior-exit']
        assert session['_lifecycle_errors'] == ['bridge-init', 'bridge-exit']
        assert ar._RUNTIME_CALLBACKS is global_callbacks
        assert ar._RUNTIME_CALLBACKS['on_init'] is recording_init
        assert ar._RUNTIME_CALLBACKS['on_process_exit'] is recording_exit
        assert injected_callbacks is not global_callbacks
    finally:
        global_callbacks['on_init'] = prior_init
        global_callbacks['on_process_exit'] = prior_exit
