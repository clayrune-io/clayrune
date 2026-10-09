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
    assert launch_env(trigger_type, base={'A': '1'}) == {
        'A': '1', LAUNCH_MARKER_ENV: '1', launch_marker.RETRY_WATCHDOG_ENV: '1'}


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
    gates = {}
    gate_cwds = []
    popen_cwds = []

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
                cwd_kw = next((k.value for k in n.keywords if k.arg == 'cwd'), None)
                popen_cwds.append((n.lineno, ast.unparse(cwd_kw) if cwd_kw else None))
            # The fail-closed gate (mc/fence_fail_closed.py): a launch_cmd(...)
            # call per Claude Popen in the same function, or the unattended
            # self-test and the onFailure settings copy are skipped at that site.
            # (Assigned to `cmd` ahead of Popen, not nested in its args: the
            # provider-architecture guard follows the name `cmd`.)
            if isinstance(f, ast.Attribute) and f.attr == 'launch_cmd':
                fn = self.stack[-1] if self.stack else '<module>'
                gates[fn] = gates.get(fn, 0) + 1
                cwd_arg = n.args[2] if len(n.args) > 2 else None
                gate_cwds.append((n.lineno, ast.unparse(cwd_arg) if cwd_arg else None))
            self.generic_visit(n)

    V().visit(tree)
    _popen_sites.gates = gates
    _popen_sites.cwds = (gate_cwds, popen_cwds)
    return sites


def test_every_claude_popen_in_agent_routes_passes_launch_env():
    sites = _popen_sites(PROJECT_ROOT / 'mc' / 'blueprints' / 'agent_routes.py')
    unmarked = [(ln, fn) for ln, fn, ok in sites if not ok and fn not in _NOT_CLAUDE]
    assert not unmarked, f'Claude spawn sites without env=launch_env(...): {unmarked}'
    assert sum(1 for _, fn, ok in sites if ok) == 11


def test_every_claude_popen_in_agent_routes_passes_cmd_through_launch_cmd():
    sites = _popen_sites(PROJECT_ROOT / 'mc' / 'blueprints' / 'agent_routes.py')
    per_fn = {}
    for _ln, fn, ok in sites:
        if ok:
            per_fn[fn] = per_fn.get(fn, 0) + 1
    short = {fn: (n, _popen_sites.gates.get(fn, 0)) for fn, n in per_fn.items()
             if _popen_sites.gates.get(fn, 0) < n}
    assert not short, f'Claude spawn sites without a launch_cmd(...) gate (popens, gates): {short}'


def test_every_launch_cmd_gate_gets_the_cwd_its_popen_uses():
    """The project fence entry that gets shadowed is the one in the child's cwd,
    so each gate must be handed the SAME expression as the Popen right after it."""
    for rel in (('mc', 'blueprints', 'agent_routes.py'), ('server.py',)):
        sites = _popen_sites(PROJECT_ROOT.joinpath(*rel))
        gate_cwds, popen_cwds = _popen_sites.cwds
        claude = [(ln, cwd) for (ln, cwd), (_l, _fn, ok) in zip(popen_cwds, sites) if ok]
        gates = sorted(gate_cwds)
        assert len(gates) == len(claude), (rel, len(gates), len(claude))
        for (gl, gcwd), (pl, pcwd) in zip(gates, sorted(claude)):
            assert gcwd == pcwd and abs(gl - pl) < 20, (rel, gl, gcwd, pl, pcwd)


def test_server_followup_hook_passes_launch_env():
    sites = _popen_sites(PROJECT_ROOT / 'server.py')
    assert [(fn, ok) for _, fn, ok in sites if fn == '_claude_followup_hook'] == [
        ('_claude_followup_hook', True)]
    assert _popen_sites.gates.get('_claude_followup_hook', 0) >= 1


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
    # Unattended spawns self-test the fence first (mc/fence_fail_closed.py); the
    # refusal itself is covered below and in tests/test_fence_fail_closed_unattended.py.
    from mc import fence_fail_closed
    self_test = {'result': (True, 'ok'), 'calls': 0}

    def fake_self_test(**k):
        self_test['calls'] += 1
        return self_test['result']
    monkeypatch.setattr(fence_fail_closed, 'claude_fence_self_test', fake_self_test)

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
               'spawned': spawned, 'set_mode': set_mode, 'monkeypatch': monkeypatch,
               'self_test': self_test}
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


# ── unattended launches are refused when the fence self-test fails ────────

@pytest.mark.parametrize('streaming', [False, True], ids=['modeA', 'modeB'])
@pytest.mark.parametrize('trigger_type', ['dispatch', 'schedule', 'workflow',
                                          'hivemind_worker'])
def test_dispatch_refuses_an_unattended_launch_whose_fence_self_test_fails(
        env, streaming, trigger_type):
    from mc.fence_fail_closed import ClaudeFenceSelfTestError
    env['set_mode'](streaming)
    env['self_test']['result'] = (False, 'exit 1; stderr: ')
    with pytest.raises(ClaudeFenceSelfTestError):
        env['ar']._dispatch_agent_internal('p1', 'do the thing', trigger_type=trigger_type)
    assert env['spawned'] == [], 'Popen must not be reached when the fence cannot run'
    assert env['self_test']['calls'] == 1


@pytest.mark.parametrize('streaming', [False, True], ids=['modeA', 'modeB'])
def test_dispatch_never_self_tests_an_attended_launch(env, streaming):
    env['set_mode'](streaming)
    env['self_test']['result'] = (False, 'would refuse if it ran')
    env['ar']._dispatch_agent_internal('p1', 'do the thing', trigger_type='manual')
    assert len(env['spawned']) == 1
    assert env['self_test']['calls'] == 0


def test_followup_respawn_of_an_unattended_session_self_tests_again(env):
    env['set_mode'](False)
    sid = env['ar']._dispatch_agent_internal('p1', 'do the thing', trigger_type='schedule')
    session = env['sessions'][sid]
    session['proc']._alive = False
    env['self_test']['result'] = (False, 'fence broke between turns')
    env['ar']._auto_dispatch_followup(session, 'next')
    # This site already turns a Popen-time exception into a log line and a
    # return; the refusal rides that path and the child is never started.
    assert len(env['spawned']) == 1, 'the respawn must not have been started'
    assert any('refusing unattended Claude launch' in str(l)
               for l in session['log_lines']), list(session['log_lines'])[-3:]
