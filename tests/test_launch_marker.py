"""Backlog dc480ad3 (MC-1040): every Claude child the server launches for an
unattended trigger_type carries CLAYRUNE_LAUNCHED_UNATTENDED=1, so the fence
hook can arm without a server lookup. This file covers the server side:

  * the helper (`mc.launch_marker.launch_env`)
  * a structural guard: every Popen in agent_routes.py is either marked or on
    a short non-Claude allowlist, so a new spawn site cannot silently skip it
  * the real spawn paths (dispatch A/B, per-turn follow-up respawn, revive)
    capture the `env=` they hand to Popen

The hook half (arms on the marker only when the lookup is None; the lookup wins
when it answers) is tests/test_fence_launch_marker.py.
"""
import ast
import io
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import launch_marker  # noqa: E402
from mc.launch_marker import LAUNCH_MARKER_ENV, launch_env  # noqa: E402

UNATTENDED = ['dispatch', 'schedule', 'workflow', 'hivemind_orchestrator', 'hivemind_worker']


# ── helper ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('trigger_type', UNATTENDED)
def test_unattended_trigger_types_set_the_marker(trigger_type):
    assert launch_env(trigger_type, base={'A': '1'}) == {'A': '1', LAUNCH_MARKER_ENV: '1'}


@pytest.mark.parametrize('trigger_type', ['manual', '', None, 'some_future_type'])
def test_attended_or_unknown_trigger_types_do_not(trigger_type):
    assert LAUNCH_MARKER_ENV not in launch_env(trigger_type, base={'A': '1'})


def test_an_inherited_marker_is_stripped_for_attended_launches():
    # A server started from an agent's shell inherits the marker; the attended
    # chats it spawns must not.
    env = launch_env('manual', base={LAUNCH_MARKER_ENV: '1', 'A': '1'})
    assert env == {'A': '1'}


def test_default_base_is_a_copy_of_the_server_environ(monkeypatch):
    monkeypatch.setenv('MC_PROBE_VAR', 'x')
    env = launch_env('dispatch')
    assert env['MC_PROBE_VAR'] == 'x'
    assert env is not launch_marker.os.environ


def test_fence_config_flag_off_means_no_marker(monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', False)
    assert LAUNCH_MARKER_ENV not in launch_env('dispatch', base={})


def test_trigger_type_set_is_pinned_to_the_fence():
    from steward import fence
    assert launch_marker.UNATTENDED_TRIGGER_TYPES == frozenset(fence._UNATTENDED_TRIGGER_TYPES)


# ── structural guard ──────────────────────────────────────────────────────

# Popen sites that never start a Claude agent session.
_NOT_CLAUDE = {'_launch_terminal_for_binary', 'agent_auth_login_remote'}


def _popen_sites(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    sites = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def visit_FunctionDef(self, n):
            self.stack.append(n.name)
            self.generic_visit(n)
            self.stack.pop()

        def visit_Call(self, n):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr == 'Popen':
                env = next((k.value for k in n.keywords if k.arg == 'env'), None)
                marked = (isinstance(env, ast.Call)
                          and isinstance(env.func, ast.Attribute)
                          and env.func.attr == 'launch_env')
                sites.append((n.lineno, self.stack[-1] if self.stack else '<module>', marked))
            self.generic_visit(n)

    V().visit(tree)
    return sites


def test_every_claude_popen_in_agent_routes_passes_launch_env():
    sites = _popen_sites(PROJECT_ROOT / 'mc' / 'blueprints' / 'agent_routes.py')
    unmarked = [(ln, fn) for ln, fn, ok in sites if not ok and fn not in _NOT_CLAUDE]
    assert not unmarked, f'Claude spawn sites without env=launch_env(...): {unmarked}'
    assert sum(1 for _, fn, ok in sites if ok) == 11


def test_server_followup_hook_passes_launch_env():
    sites = _popen_sites(PROJECT_ROOT / 'server.py')
    assert [(fn, ok) for _, fn, ok in sites if fn == '_claude_followup_hook'] == [
        ('_claude_followup_hook', True)]


# ── real spawn paths ──────────────────────────────────────────────────────

class _Stdin:
    def write(self, s):
        pass

    def flush(self):
        pass

    def close(self):
        pass


class _FakeProc:
    def __init__(self, pid=909090):
        self.pid = pid
        self.stdin = _Stdin()
        self.stdout = io.StringIO('')
        self._alive = True

    def poll(self):
        return None if self._alive else 0


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint + runs wire())
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la
    from mc.delegation_delivery import DeliveryStore

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}

    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delegation.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._memory_turn, 'seed_delivered', lambda *a, **k: None)
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (None, ''))
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda *a, **k: (str(project_path), False))
    monkeypatch.setattr(ar, '_check_context_budget', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'context')
    monkeypatch.setattr(ar, '_dispatch_with_routing_parallel',
                        lambda p, task, context_builder=None, **k: (
                            'sonnet', 'auto', [], 'context', ''))
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setitem(mc_state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(mc_state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setitem(mc_state.CONFIG, 'fence_unattended_enabled', True)
    monkeypatch.setenv(LAUNCH_MARKER_ENV, '')  # a marker leaking in from the host must not matter
    monkeypatch.delenv(LAUNCH_MARKER_ENV)

    spawned = []

    def fake_popen(cmd, **kwargs):
        proc = _FakeProc(pid=909090 + len(spawned))
        spawned.append({'proc': proc, 'cmd': cmd, 'env': kwargs.get('env')})
        return proc

    monkeypatch.setattr(ar.subprocess, 'Popen', fake_popen)

    class _InertThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(ar.threading, 'Thread', _InertThread)

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True

    def set_mode(streaming):
        monkeypatch.setitem(mc_state.CONFIG, 'use_streaming_agent', streaming)

    try:
        yield {'ar': ar, 'sessions': mc_state.agent_sessions, 'project': project,
               'spawned': spawned, 'set_mode': set_mode, 'monkeypatch': monkeypatch}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _marked(spawn):
    return (spawn['env'] or {}).get(LAUNCH_MARKER_ENV) == '1'


@pytest.mark.parametrize('streaming', [False, True], ids=['modeA', 'modeB'])
@pytest.mark.parametrize('trigger_type,expected', [
    ('dispatch', True), ('schedule', True), ('workflow', True),
    ('hivemind_worker', True), ('manual', False),
])
def test_dispatch_marks_unattended_launches_only(env, streaming, trigger_type, expected):
    env['set_mode'](streaming)
    env['ar']._dispatch_agent_internal('p1', 'do the thing', trigger_type=trigger_type)
    assert len(env['spawned']) == 1
    assert env['spawned'][0]['env'] is not None, 'Popen must get an explicit env'
    assert _marked(env['spawned'][0]) is expected


def test_followup_respawn_carries_the_sessions_stored_trigger_type(env):
    env['set_mode'](False)
    sid = env['ar']._dispatch_agent_internal('p1', 'do the thing', trigger_type='schedule')
    session = env['sessions'][sid]
    session['proc']._alive = False
    env['ar']._auto_dispatch_followup(session, 'next')
    assert len(env['spawned']) == 2
    assert _marked(env['spawned'][1])


def test_followup_respawn_of_an_attended_session_stays_unmarked(env):
    env['set_mode'](False)
    sid = env['ar']._dispatch_agent_internal('p1', 'do the thing', trigger_type='manual')
    session = env['sessions'][sid]
    session['proc']._alive = False
    env['ar']._auto_dispatch_followup(session, 'next')
    assert not _marked(env['spawned'][1])


def test_human_attend_restamp_drops_the_marker_on_the_next_respawn(env):
    # The attend route rewrites session['trigger_type'] to 'manual'; the lookup
    # then answers 'manual' and so must the next launch.
    env['set_mode'](False)
    sid = env['ar']._dispatch_agent_internal('p1', 'do the thing', trigger_type='dispatch')
    session = env['sessions'][sid]
    session['trigger_type'] = 'manual'
    session['proc']._alive = False
    env['ar']._auto_dispatch_followup(session, 'next')
    assert not _marked(env['spawned'][1])


@pytest.mark.parametrize('trigger_type,expected', [('dispatch', True), ('manual', False)])
def test_revive_from_agent_log_uses_the_logged_trigger_type(env, tmp_path, trigger_type, expected):
    env['set_mode'](False)
    ar = env['ar']
    entry = {'session_id': 'old-sid', 'claude_session_id': 'claude-old',
             'trigger_type': trigger_type, 'project_id': 'p1', 'task': 'earlier',
             'provider': 'claude'}
    env['monkeypatch'].setattr(ar, '_load_agent_log', lambda pid: [entry])
    ar._revive_from_agent_log('p1', 'old-sid', 'hello again', env['project'])
    assert len(env['spawned']) == 1, 'revive did not reach Popen'
    assert _marked(env['spawned'][0]) is expected
