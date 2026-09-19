"""Mid-turn context rollover (mc/midturn_rollover.py + the two Claude stream
readers in agent_routes).

The token trigger used to be evaluated only when a message ARRIVED. A
dispatched worker with one prompt and a long tool loop crossed the threshold
mid-turn and nothing looked (docs/_journal/rollover-not-firing.md). These tests
drive the REAL Mode B reader over a scripted stdout so they fail on the old
behaviour (no roll at all) and pin: one roll, at a tool boundary, carrying the
task + git state, keeping the MC session_id and spawner callback, and nothing
at all when `midturn_rollover_enabled` is off.

Never spawns a real model CLI.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CSID = '3f6c790f-9ec2-4ded-9793-970a6a2f340b'
TASK = 'Refactor the widget cache and keep the public API stable. (ORIGINAL TASK)'
_REAL_POPEN = subprocess.Popen


class _Stdin:
    def __init__(self):
        self.written = []

    def write(self, s):
        self.written.append(s)

    def flush(self):
        pass

    def close(self):
        pass


class _Proc:
    def __init__(self, stdout=(), pid=424242):
        self.pid = pid
        self.stdin = _Stdin()
        self.stdout = stdout

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


def _assistant(text_or_tool, ctx, mid):
    """One streamed assistant message whose usage totals `ctx` context tokens."""
    block = ({'type': 'tool_use', 'id': text_or_tool, 'name': 'Bash',
              'input': {'command': f'echo {text_or_tool}', 'description': 'probe'}}
             if text_or_tool.startswith('tool') else
             {'type': 'text', 'text': text_or_tool})
    return json.dumps({'type': 'assistant', 'session_id': CSID, 'message': {
        'id': mid, 'content': [block],
        'usage': {'input_tokens': ctx, 'cache_read_input_tokens': 0,
                  'cache_creation_input_tokens': 0, 'output_tokens': 10}}}) + '\n'


def _tool_result(tool_id):
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': tool_id, 'content': 'ok'}]}}) + '\n'


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint)
    from mc import state as mc_state
    from mc import midturn_rollover
    from mc.blueprints import agent_routes as ar
    from mc.delegation_delivery import DeliveryStore

    project_path = tmp_path / 'proj'
    project_path.mkdir()
    for args in (['init', '-q'], ['config', 'user.email', 't@example.invalid'],
                 ['config', 'user.name', 't']):
        subprocess.run(['git', *args], cwd=project_path, check=True)
    (project_path / 'tracked.txt').write_text('a\n')
    subprocess.run(['git', 'add', 'tracked.txt'], cwd=project_path, check=True)
    subprocess.run(['git', 'commit', '-q', '-m', 'init'], cwd=project_path, check=True)
    (project_path / 'tracked.txt').write_text('a\nb\n')          # unstaged diff
    (project_path / 'new_untracked_file.py').write_text('x = 1\n')  # untracked

    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}
    real_reader = ar._read_agent_stream_b
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delegation.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setitem(mc_state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(mc_state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 200_000)
    monkeypatch.setitem(mc_state.CONFIG, 'midturn_rollover_enabled', True)
    monkeypatch.setattr(ar, '_session_too_large', lambda pp, sid: (False, 4096))
    monkeypatch.setattr(ar, '_kill_proc_background', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_unregister_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda *a, **k: None)  # new proc's reader
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_fresh_context_for', lambda *a, **k: 'FRESH CONTEXT')
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda *a, **k: ([], None))
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(midturn_rollover, '_log_dir', lambda: tmp_path / 'midturn_log')

    activity = []
    monkeypatch.setattr(ar, '_log_agent_activity', lambda pid, line: activity.append(line))
    notified = []
    monkeypatch.setattr(ar, '_notify_agent_spawner',
                        lambda pid, sid, child, summary: notified.append((pid, sid, summary)))

    spawned = []
    new_proc = _Proc(pid=999999)

    def _popen(cmd, **kwargs):
        if cmd and cmd[0] == 'git':  # midturn_rollover._git shells out through Popen
            return _REAL_POPEN(cmd, **kwargs)
        if '-p' in cmd:  # Scribe/distiller one-shots fired by reader teardown
            raise FileNotFoundError('one-shot model calls are stubbed out')
        spawned.append(cmd)
        return new_proc

    monkeypatch.setattr(ar.subprocess, 'Popen', _popen)

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield {'ar': ar, 'reader': real_reader, 'sessions': mc_state.agent_sessions,
               'activity': activity, 'notified': notified, 'spawned': spawned,
               'new_proc': new_proc, 'tmp': tmp_path, 'pp': project_path,
               'CONFIG': mc_state.CONFIG}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _session(proc, **extra):
    s = {
        'session_id': 'worker-1', 'project_id': 'p1', 'claude_session_id': CSID,
        'status': 'running', 'task': TASK, 'log_lines': [],
        'started_at': '2026-09-19T00:00:00Z', 'mode': 'B', 'proc': proc,
        'process_alive': True, 'stdin_lock': threading.Lock(),
        'last_output_time': 0.0, 'last_status_change_time': 0.0,
        'context_tokens': None, '_notify_session': 'spawner-1',
        'provider': 'claude',
    }
    s.update(extra)
    return s


def _run_reader(env, lines_fn):
    """Run the real Mode B reader over `lines_fn()` (a generator of stdout lines)."""
    proc = _Proc(stdout=lines_fn())
    session = _session(proc)
    env['sessions']['worker-1'] = session
    env['reader'](proc, session)
    return session


def test_crossing_mid_turn_rolls_once_at_a_tool_boundary(env):
    """Threshold crossed inside ONE turn (no message arrives): exactly one
    fresh spawn, and only AFTER the tool_result — never while t1 is in flight."""
    spawned_at_yield = {}

    def lines():
        yield _assistant('tool1', 120_000, 'm1')
        yield _tool_result('tool1')            # under threshold: no roll
        spawned_at_yield['under'] = len(env['spawned'])
        yield _assistant('tool2', 250_000, 'm2')   # crossed, tool call now in flight
        spawned_at_yield['in_flight'] = len(env['spawned'])
        yield _tool_result('tool2')            # boundary: roll here
        # everything after the roll is discarded by the reader (ownership gate)
        yield _assistant('tool3', 260_000, 'm3')
        yield _tool_result('tool3')

    session = _run_reader(env, lines)

    assert spawned_at_yield == {'under': 0, 'in_flight': 0}, spawned_at_yield
    assert _wait(lambda: len(env['spawned']) == 1), env['spawned']
    assert len(env['spawned']) == 1, 'must roll exactly once'
    assert '-r' not in env['spawned'][0], 'roll must start FRESH, not resume'
    assert [a for a in env['activity'] if 'Auto-fresh: context 250k' in a], env['activity']
    # MC session identity and spawner wiring survive the roll.
    assert env['sessions']['worker-1'] is session
    assert session['session_id'] == 'worker-1'
    assert session['_notify_session'] == 'spawner-1'
    assert _wait(lambda: session.get('proc') is env['new_proc'])
    assert '_mt_roll_requested' not in session
    # A second boundary check right after the roll must not roll again.
    env['ar']._maybe_midturn_roll(session)
    assert len(env['spawned']) == 1


def test_handoff_carries_task_git_state_and_recent_tool_calls(env):
    def lines():
        yield _assistant('tool1', 250_000, 'm1')
        yield _tool_result('tool1')

    _run_reader(env, lines)
    assert _wait(lambda: env['new_proc'].stdin.written), 'fresh session got no prompt'
    prompt = json.loads(env['new_proc'].stdin.written[0])['message']['content']

    assert TASK in prompt, 'original task must be carried verbatim'
    assert str(env['pp']) in prompt
    assert 'Branch:' in prompt
    assert 'new_untracked_file.py' in prompt, 'git status --short missing'
    assert 'tracked.txt' in prompt and '1 file changed' in prompt, 'git diff --stat missing'
    assert 'Bash: echo tool1' in prompt, 'last tool calls missing'
    assert 'Background jobs' in prompt
    assert 'rolled over mid-task' in prompt


def test_roll_is_logged_to_a_durable_file(env):
    def lines():
        yield _assistant('tool1', 250_000, 'm1')
        yield _tool_result('tool1')

    _run_reader(env, lines)
    log = env['tmp'] / 'midturn_log' / 'p1.jsonl'
    assert _wait(log.is_file), 'no durable roll record written'
    rows = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]['session_id'] == 'worker-1'
    assert rows[0]['context_tokens'] == 250_000
    assert rows[0]['threshold'] == 200_000


def test_spawner_callback_still_fires_after_the_roll(env):
    """The roll re-arms the completion callback for the fresh turn, and the
    child's `_notify_session` still points at the spawner, so when the rolled
    session finishes the spawner hears about it once."""
    def lines():
        yield _assistant('tool1', 250_000, 'm1')
        yield _tool_result('tool1')

    session = _run_reader(env, lines)
    assert _wait(lambda: session.get('proc') is env['new_proc'])
    assert env['notified'] == [], 'roll itself must not report completion'

    env['ar']._maybe_notify_spawner(session, 'all done')
    assert env['notified'] == [('p1', 'spawner-1', 'all done')]
    env['ar']._maybe_notify_spawner(session, 'all done')
    assert len(env['notified']) == 1, 'callback is latched once per turn'


def test_flag_off_changes_nothing(env):
    env['CONFIG']['midturn_rollover_enabled'] = False

    def lines():
        yield _assistant('tool1', 250_000, 'm1')
        yield _tool_result('tool1')
        yield _assistant('tool2', 300_000, 'm2')
        yield _tool_result('tool2')

    session = _run_reader(env, lines)
    time.sleep(0.2)
    assert env['spawned'] == []
    assert not [a for a in env['activity'] if 'Auto-fresh' in a]
    assert not (env['tmp'] / 'midturn_log').exists()
    assert not [k for k in session if k.startswith('_mt_')], \
        'flag off must not even record bookkeeping'
    assert session['proc'].stdin.written == []


def test_parallel_tool_calls_wait_for_every_result(env):
    """Two tool calls in one assistant turn: the first result arriving is NOT
    a boundary — the second call is still running."""
    ar = env['ar']
    session = _session(_Proc())
    env['sessions']['worker-1'] = session
    session['context_tokens'] = 250_000
    ar._midturn.note_tool_use(session, {'type': 'tool_use', 'id': 'a', 'name': 'Read',
                                        'input': {'file_path': 'x'}})
    ar._midturn.note_tool_use(session, {'type': 'tool_use', 'id': 'b', 'name': 'Read',
                                        'input': {'file_path': 'y'}})
    ar._midturn.note_tool_results(session, [{'type': 'tool_result', 'tool_use_id': 'a'}])
    assert not ar._midturn.should_roll(session, ar._context_tokens_over_threshold)
    ar._midturn.note_tool_results(session, [{'type': 'tool_result', 'tool_use_id': 'b'}])
    assert ar._midturn.should_roll(session, ar._context_tokens_over_threshold)


def test_non_claude_and_unknown_tokens_never_roll(env):
    ar = env['ar']
    s = _session(_Proc(), context_tokens=250_000, provider='gemini')
    assert not ar._midturn.should_roll(s, ar._context_tokens_over_threshold)
    s = _session(_Proc(), context_tokens=None)
    assert not ar._midturn.should_roll(s, ar._context_tokens_over_threshold)
    env['CONFIG']['context_rollover_tokens'] = 0
    s = _session(_Proc(), context_tokens=999_999)
    assert not ar._midturn.should_roll(s, ar._context_tokens_over_threshold)
