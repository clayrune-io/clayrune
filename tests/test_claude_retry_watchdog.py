"""CLAUDE_CODE_RETRY_WATCHDOG for unattended Claude launches (2026-10-03).

Without the variable a `claude -p` run that gets a 429 ends in ~1 s ("API Error:
Request rejected (429)"). With it the process stays alive and waits out the
reset, emitting `system/api_retry` every ~30 s. Measured against a fake local
429 endpoint with a dummy key (Quill, claude 2.1.287): attempt 1, max_retries
300, retry_delay_ms 899715 -> 869715 -> 839715.

Three pieces, all pinned here:
  1. the launch env (mc.launch_marker.launch_env, one env per spawn site,
     composed with the fence launch marker): set for exactly the fence's
     unattended trigger types, never for an attended chat, never over an
     explicit operator value;
  2. ClaudeRuntime.parse_event turns the event into API_RETRY with a visible line;
  3. both Claude stdout readers treat it as output (last_output_time), because
     Guardian State 2 kills a `running` proc silent > 600 s with idle CPU, and a
     rate-limit wait is exactly that.
"""
import ast
import importlib
import io
import json
import subprocess
from pathlib import Path

import pytest

import mc.agent_runtime as art
from mc import launch_marker
from mc.launch_marker import launch_env
from steward import fence
from tests.test_revive_notify_carry import ar, _project, CSID  # noqa: F401

VAR = 'CLAUDE_CODE_RETRY_WATCHDOG'
REPO = Path(__file__).resolve().parent.parent

# Shape of the line Claude Code 2.1.287 printed for a 15 minute 429 wait.
API_RETRY_LINE = json.dumps({
    'type': 'system', 'subtype': 'api_retry', 'attempt': 1, 'max_retries': 300,
    'retry_delay_ms': 899715, 'error_status': 429, 'error': 'rate_limit',
    'session_id': '11111111-2222-3333-4444-555555555555',
    'uuid': '99999999-8888-7777-6666-555555555555'})


def _line(delay_ms, attempt=1, status=429, error='rate_limit', cap=300):
    return json.dumps({'type': 'system', 'subtype': 'api_retry', 'attempt': attempt,
                       'max_retries': cap, 'retry_delay_ms': delay_ms,
                       'error_status': status, 'error': error})


# ── 1. launch env ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('trigger_type', sorted(fence._UNATTENDED_TRIGGER_TYPES))
def test_env_set_for_every_trigger_type_the_fence_calls_unattended(trigger_type, monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    env = launch_env(trigger_type)
    assert env[VAR] == '1'
    # composed with, not replacing, the fence launch marker
    assert env[launch_marker.LAUNCH_MARKER_ENV] == '1'
    # a full copy of the server env, not a bare dict (Popen(env=) replaces it)
    assert env.get('PATH') == art.os.environ.get('PATH')


def test_unattended_set_is_the_fences_own_contents_not_a_second_list():
    assert launch_marker.UNATTENDED_TRIGGER_TYPES == fence._UNATTENDED_TRIGGER_TYPES
    assert {'schedule', 'workflow', 'dispatch', 'hivemind_orchestrator',
            'hivemind_worker'} == set(fence._UNATTENDED_TRIGGER_TYPES)


@pytest.mark.parametrize('trigger_type', ['manual', '', None, 'some_future_type'])
def test_env_untouched_for_attended_or_unknown(trigger_type, monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    assert VAR not in launch_env(trigger_type)


@pytest.mark.parametrize('value', ['0', '1', 'false'])
def test_explicit_operator_value_is_respected(value, monkeypatch):
    monkeypatch.setenv(VAR, value)
    assert launch_env('schedule')[VAR] == value  # an explicit 0 stays 0


def test_empty_operator_value_counts_as_unset(monkeypatch):
    monkeypatch.setenv(VAR, '')
    assert launch_env('schedule')[VAR] == '1'


def test_watchdog_does_not_depend_on_the_fence_switch(monkeypatch):
    from mc import state
    monkeypatch.delenv(VAR, raising=False)
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', False)
    env = launch_env('schedule')
    assert env[VAR] == '1' and launch_marker.LAUNCH_MARKER_ENV not in env


def test_helper_does_not_mutate_the_server_environment(monkeypatch):
    monkeypatch.delenv(VAR, raising=False)
    launch_env('schedule')
    assert VAR not in art.os.environ


def test_no_second_watchdog_env_helper_or_env_kwarg_left_in_the_spawn_sites():
    """Both branches once added `env=` to the same Popen; the merge was a
    SyntaxError. The only env= on a Claude spawn is launch_env."""
    for rel in ('mc/blueprints/agent_routes.py', 'server.py', 'mc/agent_runtime.py'):
        assert 'claude_retry_watchdog_env' not in (REPO / rel).read_text(encoding='utf-8')


def test_every_claude_popen_in_the_routes_passes_env():
    """A new spawn site added without env= would silently launch an unattended
    run on the old fail-in-one-second behaviour."""
    for rel in ('mc/blueprints/agent_routes.py', 'server.py'):
        tree = ast.parse((REPO / rel).read_text(encoding='utf-8'))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = list(ast.walk(fn))
            if not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                       and n.func.id == '_resolve_claude' for n in nodes):
                continue
            for n in nodes:
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == 'Popen'):
                    assert 'env' in {k.arg for k in n.keywords}, (
                        f'{rel}:{n.lineno} in {fn.name}: claude Popen without env=')


def _record_popen(ar, monkeypatch):
    seen = []

    class _P:
        pid = 777
        stdout = iter([])
        stdin = type('S', (), {'write': lambda s, x: None, 'flush': lambda s: None,
                               'close': lambda s: None})()

        def poll(self):
            return None

    def fake(*a, **kw):
        seen.append(kw)
        return _P()

    monkeypatch.setattr(ar.subprocess, 'Popen', fake)
    return seen


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('trigger_type,expect', [
    ('schedule', True), ('workflow', True), ('dispatch', True),
    ('hivemind_worker', True), ('hivemind_orchestrator', True), ('manual', False)])
def test_revive_launch_env_follows_the_rows_trigger_type(
        ar, tmp_path, monkeypatch, streaming, trigger_type, expect):
    monkeypatch.delenv(VAR, raising=False)
    monkeypatch.setitem(ar.state.CONFIG, 'use_streaming_agent', streaming)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **kw: None)
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: [dict(
        session_id='s1', claude_session_id=CSID, trigger_type=trigger_type,
        trigger_id='t1')])
    seen = _record_popen(ar, monkeypatch)
    assert ar._revive_from_agent_log('p1', 's1', 'go', _project(tmp_path)) is not None
    assert len(seen) == 1
    env = seen[0].get('env')
    assert env is not None
    assert (env.get(VAR) == '1') is expect
    assert (env.get(launch_marker.LAUNCH_MARKER_ENV) == '1') is expect


@pytest.mark.parametrize('trigger_type,expect', [('schedule', True), ('manual', False)])
def test_queued_followup_respawn_env_follows_the_sessions_trigger_type(
        ar, tmp_path, monkeypatch, trigger_type, expect):
    monkeypatch.delenv(VAR, raising=False)
    project = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *a: ([], None))
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'fake')
    seen = _record_popen(ar, monkeypatch)
    session = dict(project_id='p1', session_id='s1', model='sonnet', log_lines=[],
                   claude_session_id=CSID, trigger_type=trigger_type)
    ar._auto_dispatch_followup(session, 'continue')
    assert len(seen) == 1
    env = seen[0].get('env')
    assert env is not None
    assert (env.get(VAR) == '1') is expect
    assert (env.get(launch_marker.LAUNCH_MARKER_ENV) == '1') is expect


# ── 2. parse_event ───────────────────────────────────────────────────────────

def test_parse_event_handles_a_real_shaped_api_retry_line():
    ev = art.ClaudeRuntime().parse_event(API_RETRY_LINE, 'mc1')
    assert ev is not None and ev.type == art.EventType.API_RETRY
    assert ev.provider == 'claude' and ev.mc_session_id == 'mc1'
    assert ev.session_id == '11111111-2222-3333-4444-555555555555'
    assert ev.payload['attempt'] == 1 and ev.payload['max_retries'] == 300
    assert ev.payload['retry_delay_ms'] == 899715 and ev.payload['error_status'] == 429
    assert ev.payload['text'] == (
        '[Waiting for usage limit reset, next try in 15 min (attempt 1/300, HTTP 429)]')
    assert ev.raw['subtype'] == 'api_retry'


@pytest.mark.parametrize('delay_ms,label', [
    (0, 'under a minute'), (1000, 'under a minute'), (59999, 'under a minute'),
    (60000, '1 min'), (89000, '1 min'), (90000, '2 min'), (869715, '14 min'),
    (899715, '15 min'), (3_540_000, '59 min'), (3_600_000, '1 h'),
    (4_500_000, '1 h 15 min'), (18_000_000, '5 h'), (None, 'a moment'), ('x', 'a moment')])
def test_wait_label(delay_ms, label):
    assert art._retry_wait_label(delay_ms) == label


def test_non_429_retry_is_not_called_a_usage_limit():
    ev = art.ClaudeRuntime().parse_event(_line(2000, attempt=2, status=529,
                                                error='overloaded', cap=10))
    assert ev.payload['text'] == (
        '[API request failed, next try in under a minute (attempt 2/10, HTTP 529)]')
    ev = art.ClaudeRuntime().parse_event(_line(2000, status=None, error='unknown'))
    assert 'no response' in ev.payload['text'] and 'usage limit' not in ev.payload['text']


def test_other_system_events_are_still_unhandled():
    assert art.ClaudeRuntime().parse_event(
        json.dumps({'type': 'system', 'subtype': 'something_new'})) is None


# ── 3. readers ───────────────────────────────────────────────────────────────

class _FakeProc:
    def __init__(self, lines):
        self.stdout = io.StringIO('\n'.join(lines) + '\n')
        self.pid = -1

    def wait(self):
        return 0

    def kill(self):
        pass


def _session():
    return {'project_id': 'p-retry', 'session_id': 'mc-retry', 'status': 'running',
            'log_lines': [], 'last_output_time': 0.0, 'last_status_change_time': 0.0,
            'provider': 'claude'}


@pytest.mark.parametrize('reader', ['_read_agent_stream', '_read_agent_stream_b'])
def test_reader_treats_api_retry_as_output_and_shows_one_line(tmp_data_dir, reader):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _session()
    # three ticks of the SAME 15 min wait, as the CLI emits them 30 s apart
    proc = _FakeProc([_line(899715), _line(869715), _line(839715)])
    session['proc'] = proc
    getattr(server, reader)(proc, session)
    assert session['last_output_time'] > 0.0, 'api_retry did not stamp last_output_time'
    assert session['log_lines'] == [
        '[Waiting for usage limit reset, next try in 15 min (attempt 1/300, HTTP 429)]']


def test_stamp_keeps_guardian_state_2_from_killing_a_waiting_run(tmp_data_dir, monkeypatch):
    server = importlib.import_module('server')
    importlib.reload(server)
    from mc.blueprints import agent_routes as routes
    now = 1_000_000.0
    session = _session()
    session['last_output_time'] = now - routes.GUARDIAN_HUNG_TIMEOUT - 1  # silent > 600 s
    ev = art.ClaudeRuntime().parse_event(API_RETRY_LINE)
    art.note_api_retry(session, ev, now=now)
    assert now - session['last_output_time'] <= routes.GUARDIAN_HUNG_TIMEOUT
    assert now - session['last_output_time'] == 0


def test_note_api_retry_throttles_one_wait_but_not_a_new_one():
    ClaudeRuntime = art.ClaudeRuntime()
    s = _session()
    seq = [  # (now, delay_ms, attempt)
        (0, 899715, 1), (30, 869715, 1), (60, 839715, 1),   # same wait counting down
        (300, 599715, 1),                                    # 5 min later: refresh
        (330, 569715, 1),                                    # quiet again
        (590, 9715, 1),                                      # 290 s after the refresh: quiet
        (620, 2000, 2),                                      # attempt changed: new wait
    ]
    shown = []
    for now, delay, attempt in seq:
        before = len(s['log_lines'])
        art.note_api_retry(s, ClaudeRuntime.parse_event(_line(delay, attempt=attempt)), now=now)
        shown.append(len(s['log_lines']) > before)
        assert s['last_output_time'] == now          # stamped on EVERY event
    assert shown == [True, False, False, True, False, False, True]


def test_delay_going_up_is_a_new_wait_and_is_shown():
    rt = art.ClaudeRuntime()
    s = _session()
    art.note_api_retry(s, rt.parse_event(_line(20000)), now=0)
    art.note_api_retry(s, rt.parse_event(_line(600000)), now=40)   # next 429, same attempt
    assert len(s['log_lines']) == 2


def test_api_retry_json_never_lands_in_the_chat(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _session()
    proc = _FakeProc([API_RETRY_LINE])
    session['proc'] = proc
    server._read_agent_stream(proc, session)
    assert not any('api_retry' in ln or '{' in ln for ln in session['log_lines'])


# ── 4. the guardian's 10 minute stall kill, against the real tick ─────────────

class _AliveProc:
    pid = 424242

    def poll(self):
        return None


def _guardian_session(last_output_ago, now):
    return {'project_id': 'p-retry', 'session_id': 'mc-retry', 'status': 'running',
            'mode': 'B', 'proc': _AliveProc(), 'provider': 'claude', 'log_lines': [],
            'last_output_time': now - last_output_ago,
            'last_status_change_time': now - last_output_ago}


@pytest.fixture
def guardian(monkeypatch):
    from mc.blueprints import agent_routes as routes
    killed = []
    monkeypatch.setattr(routes, '_proc_is_cpu_idle', lambda *a: True)  # a wait is CPU-idle
    monkeypatch.setattr(routes, '_pid_is_alive', lambda pid: True)  # fake pid: skip State 1
    monkeypatch.setattr(routes, '_kill_proc_background', killed.append)
    return routes, killed


def test_control_guardian_kills_a_claude_run_silent_past_600s(guardian):
    routes, killed = guardian
    now = routes._time.time()
    s = _guardian_session(routes.GUARDIAN_HUNG_TIMEOUT + 60, now)
    routes._guardian_check_session('mc-retry', s, now)
    assert len(killed) == 1 and s['guardian_state'] == 'needs_attention'


def test_guardian_spares_a_session_that_is_in_api_retry(guardian):
    """A rate-limit wait is silent and CPU-idle, i.e. exactly State 2's profile.
    It is spared only because each api_retry event (every ~30 s) restamps
    last_output_time through the real reader hook."""
    routes, killed = guardian
    now = routes._time.time()
    s = _guardian_session(5 * 3600, now)  # had been silent for hours before the wait
    for delay in (899715, 869715, 839715):
        routes._note_api_retry(s, _line(delay))
        routes._guardian_check_session('mc-retry', s, routes._time.time() + 30)
    assert killed == [] and 'guardian_state' not in s
    stamped = s['last_output_time']
    # inside the window after the latest event: spared
    routes._guardian_check_session('mc-retry', s, stamped + routes.GUARDIAN_HUNG_TIMEOUT - 1)
    assert killed == []
    # the watchdog only resets the clock: if the events stop for >600 s it is hung again
    routes._guardian_check_session('mc-retry', s, stamped + routes.GUARDIAN_HUNG_TIMEOUT + 1)
    assert len(killed) == 1
