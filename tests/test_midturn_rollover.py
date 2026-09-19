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


def _assistant(text_or_tool, ctx, mid, parent=None):
    """One streamed assistant message whose usage totals `ctx` context tokens.
    `parent` marks a subagent (Task) message, as the CLI does."""
    block = ({'type': 'tool_use', 'id': text_or_tool, 'name': 'Bash',
              'input': {'command': f'echo {text_or_tool}', 'description': 'probe'}}
             if text_or_tool.startswith('tool') else
             {'type': 'text', 'text': text_or_tool})
    return json.dumps({'type': 'assistant', 'session_id': CSID,
                       'parent_tool_use_id': parent, 'message': {
        'id': mid, 'content': [block],
        'usage': {'input_tokens': ctx, 'cache_read_input_tokens': 0,
                  'cache_creation_input_tokens': 0, 'output_tokens': 10}}}) + '\n'


def _tool_result(tool_id, parent=None):
    return json.dumps({'type': 'user', 'parent_tool_use_id': parent,
                       'message': {'role': 'user', 'content': [
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
    real_reader_a = ar._read_agent_stream
    real_fresh = ar._fresh_context_for
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
    events = []
    monkeypatch.setattr(ar, '_kill_proc_background', lambda *a, **k: events.append('kill'))
    monkeypatch.setattr(ar, '_unregister_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda *a, **k: None)  # new proc's reader
    monkeypatch.setattr(ar, '_read_agent_stream', lambda *a, **k: None)    # (Mode A)
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
    spawned_kwargs = []
    mode_a_spawn = []   # non-empty: a `-p` argv IS the Mode A roll, let it through
    new_proc = _Proc(pid=999999)

    def _popen(cmd, **kwargs):
        if cmd and cmd[0] == 'git':  # midturn_rollover._git shells out through Popen
            return _REAL_POPEN(cmd, **kwargs)
        if '-p' in cmd and not mode_a_spawn:  # Scribe/distiller one-shots fired by reader teardown
            raise FileNotFoundError('one-shot model calls are stubbed out')
        spawned.append(cmd)
        spawned_kwargs.append(kwargs)
        return new_proc

    monkeypatch.setattr(ar.subprocess, 'Popen', _popen)

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield {'ar': ar, 'reader': real_reader, 'reader_a': real_reader_a,
               'events': events, 'spawned_kwargs': spawned_kwargs, 'real_fresh': real_fresh, 'mode_a_spawn': mode_a_spawn,
               'sessions': mc_state.agent_sessions,
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


def _run_reader(env, lines_fn, session=None, proc=None, reader='reader', **extra):
    """Run a real reader (Mode B by default) over `lines_fn()` (a generator of
    stdout lines)."""
    proc = proc or _Proc(stdout=lines_fn())
    proc.stdout = lines_fn()
    session = session or _session(proc, **extra)
    session['proc'] = proc
    env['sessions']['worker-1'] = session
    env[reader](proc, session)
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
    session['_mt_main_tokens'] = 250_000
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
    s = _session(_Proc(), _mt_main_tokens=250_000, provider='gemini')
    assert not ar._midturn.should_roll(s, ar._context_tokens_over_threshold)
    s = _session(_Proc(), _mt_main_tokens=None)
    assert not ar._midturn.should_roll(s, ar._context_tokens_over_threshold)
    env['CONFIG']['context_rollover_tokens'] = 0
    s = _session(_Proc(), _mt_main_tokens=999_999)
    assert not ar._midturn.should_roll(s, ar._context_tokens_over_threshold)


# --- Review fixes (b5fa971 review, 2026-09-18) --------------------------------

def _tool_use_block(tid, name='Bash'):
    return {'type': 'tool_use', 'id': tid, 'name': name, 'input': {'command': 'x'}}


def _roll_lines():
    yield _assistant('tool1', 250_000, 'm1')
    yield _tool_result('tool1')


def test_finding1_roll_keeps_the_steward_task_for_the_context_rebuild(env, monkeypatch):
    """`_fresh_context_for` is NOT stubbed here. A roll's `message` is the canned
    ROLL_MESSAGE; the rebuild used to key `is_unattended_task` (and the read
    floor / positions) on it, so a steward cycle came back as an ATTENDED
    consumer of unattended-origin artifacts (CLAUDE.md learning rail 2)."""
    ar = env['ar']
    monkeypatch.setattr(ar, '_fresh_context_for', env['real_fresh'])
    seen = []

    def _floor(pid, task, topk, consumer_unattended=False):
        seen.append({'task': task, 'consumer_unattended': consumer_unattended})
        return []

    monkeypatch.setattr(ar._distiller, 'exploration_read_floor', _floor)
    steward_task = '[Steward cycle] Review the backlog and pick the next goal.'

    _run_reader(env, _roll_lines, task=steward_task)
    assert _wait(lambda: env['spawned']), 'roll did not spawn a fresh session'
    assert seen, 'read floor was never consulted on the rebuild'
    assert seen[0]['task'] == steward_task, seen
    assert seen[0]['consumer_unattended'] is True, seen


def test_finding2_kill_mid_tool_does_not_block_a_later_roll(env):
    """A proc killed with a tool call in flight never delivers its tool_result.
    That id used to stay in `_mt_pending_tools` forever, so `should_roll` was
    False for the rest of the session and the original bug came back silently."""
    ar = env['ar']
    session = _session(_Proc(pid=111))
    env['sessions']['worker-1'] = session
    ar._midturn.note_tool_use(session, _tool_use_block('orphan'))       # in flight...
    assert session['_mt_pending_tools'] == {'orphan'}
    # ...then the process is replaced (user interrupt / stop+resume / guardian).
    _run_reader(env, _roll_lines, session=session, proc=_Proc(pid=222))
    assert _wait(lambda: len(env['spawned']) == 1), \
        'orphaned tool id from the killed proc blocked the roll'


def test_finding3_mode_a_roll_with_a_huge_task_keeps_the_prompt_off_the_command_line(env):
    """cmd.exe caps a command line at 8191 chars. The Mode A roll passed the
    handoff + task (uncapped) + git state via `-p <msg>`."""
    big_task = 'Investigate the flaky importer. ' + ('detail ' * 3000)   # ~21k chars
    assert len(big_task) > 10_000

    env['mode_a_spawn'].append(True)
    session = _run_reader(env, _roll_lines, reader='reader_a', mode='A', task=big_task)
    assert _wait(lambda: len(env['spawned']) == 1), env['spawned']
    cmd = env['spawned'][0]
    assert sum(len(c) + 1 for c in cmd) < 8191, f'command line is {sum(len(c) for c in cmd)} chars'
    assert not any(big_task in c for c in cmd)
    assert env['spawned_kwargs'][0]['stdin'] == subprocess.PIPE
    assert _wait(lambda: env['new_proc'].stdin.written), 'prompt never written to stdin'
    written = ''.join(env['new_proc'].stdin.written)
    assert big_task in written
    assert 'Mid-task rollover state' in written
    assert session['session_id'] == 'worker-1'


def test_finding4_state_is_collected_after_the_kill_not_before(env, monkeypatch):
    """Three git calls (10s timeout each) ran on the reader thread while the old
    proc was still alive, so the roll could land mid tool call."""
    ar = env['ar']
    real = ar._midturn.build_state_block

    def _spy(session, cwd):
        env['events'].append('state')
        return real(session, cwd)

    monkeypatch.setattr(ar._midturn, 'build_state_block', _spy)
    _run_reader(env, _roll_lines)
    assert _wait(lambda: len(env['spawned']) == 1)
    assert env['events'] == ['kill', 'state'], env['events']


def test_finding4_a_roll_decided_before_a_newer_interrupt_is_dropped(env):
    """The reader decides to roll, then a user interrupt lands and replaces the
    process before the roll reaches the project lock. The stale roll must not
    kill the newer process or overwrite its turn."""
    ar = env['ar']
    old, newer = _Proc(pid=1), _Proc(pid=2)
    session = _session(newer, _mt_main_tokens=250_000)   # session already moved on
    env['sessions']['worker-1'] = session
    ar._maybe_midturn_roll(session, old)                  # the stale reader's call
    assert env['spawned'] == [] and env['events'] == []
    assert '_interrupting' not in session and '_mt_roll_requested' not in session
    assert session.get('_mt_roll_failures', 0) == 0, 'a superseded roll is not a failure'
    # An interrupt already in flight is dropped the same way.
    session2 = _session(old, _mt_main_tokens=250_000, _interrupting=True)
    env['sessions']['worker-1'] = session2
    payload, status = ar.agent_interrupt('p1', _internal={
        'session_id': 'worker-1', 'message': 'm', 'midturn': True, 'proc': old,
        'tokens': 250_000, 'build_state': lambda cwd: ''})
    assert status == 409 and env['spawned'] == [] and env['events'] == []


def test_finding5_failure_before_the_kill_does_not_leave_the_session_gated(env, monkeypatch):
    """`_interrupting` used to be set BEFORE `_rearm_notify_for_new_turn`, which
    writes the delegation DB and can raise. Left set, the still-live old reader
    is gated out of every status write: stuck 'running', spawner never told."""
    ar = env['ar']

    def _boom(session):
        raise RuntimeError('delegation db locked')

    monkeypatch.setattr(ar, '_rearm_notify_for_new_turn', _boom)
    old = _Proc(pid=1)
    session = _session(old, _mt_main_tokens=250_000)
    env['sessions']['worker-1'] = session
    ar._maybe_midturn_roll(session, old)
    assert '_interrupting' not in session
    assert '_mt_roll_requested' not in session
    assert ar._session_owned_by(session, old), 'old reader must still own the session'
    assert env['events'] == [] and env['spawned'] == []
    assert session['status'] == 'running'
    assert not [l for l in session['log_lines'] if 'rolled over' in l]
    assert session['_mt_roll_failures'] == 1


def test_finding6_no_reroll_loop_when_the_fresh_prefix_is_over_the_threshold(env):
    """threshold below the fresh session's own size: every tool boundary would
    roll again, forever. Re-rolling needs real growth over the fresh figure."""
    mt = env['ar']._midturn
    over = env['ar']._context_tokens_over_threshold
    session = _session(_Proc())
    mt.begin_roll(session)                       # a roll just happened
    mt.note_call_tokens(session, 250_000)        # fresh session reports 250k already
    mt.note_tool_use(session, _tool_use_block('a'))
    mt.note_tool_results(session, [{'type': 'tool_result', 'tool_use_id': 'a'}])
    assert not mt.should_roll(session, over), 'rolled again with zero growth'
    mt.note_call_tokens(session, 250_000 + mt.MIN_GROWTH_TOKENS - 1)
    assert not mt.should_roll(session, over)
    mt.note_call_tokens(session, 250_000 + mt.MIN_GROWTH_TOKENS)
    assert mt.should_roll(session, over)


def test_finding6_repeated_failed_rolls_stop_being_attempted(env):
    mt = env['ar']._midturn
    over = env['ar']._context_tokens_over_threshold
    session = _session(_Proc(), _mt_main_tokens=250_000,
                       _mt_roll_failures=mt.MAX_ROLL_FAILURES)
    assert not mt.should_roll(session, over)


def test_low_roll_is_fresh_even_when_the_token_recheck_would_say_no(env):
    """The interrupt path re-ran `_auto_fresh_trigger`; with `context_tokens`
    unknown/stale it fell to the byte check (False here) and RESUMED, silently
    dropping the state block. A mid-task roll must force the fresh branch."""
    ar = env['ar']
    old = _Proc(pid=1)
    session = _session(old, context_tokens=None, _mt_main_tokens=250_000)
    env['sessions']['worker-1'] = session
    ar._maybe_midturn_roll(session, old)
    assert _wait(lambda: len(env['spawned']) == 1)
    assert '-r' not in env['spawned'][0], 'resumed instead of starting fresh'
    assert _wait(lambda: env['new_proc'].stdin.written)
    assert 'Mid-task rollover state' in env['new_proc'].stdin.written[0]


def test_low_subagent_tokens_do_not_trigger_the_parents_roll(env):
    """A Task subagent streams through the same reader; its usage is its own
    context. The Task tool_result boundary was judged on the subagent's 250k."""
    def lines():
        yield _assistant('toolTask', 100_000, 'm1')                    # parent: 100k
        yield _assistant('toolSub', 250_000, 'm2', parent='toolTask')  # subagent: 250k
        yield _tool_result('toolSub', parent='toolTask')
        yield _tool_result('toolTask')                                 # boundary

    session = _run_reader(env, lines)
    time.sleep(0.2)
    assert env['spawned'] == []
    assert session['_mt_main_tokens'] == 100_000
    assert session['context_tokens'] == 250_000, 'flag-off figure must be unchanged'


def test_low_results_for_calls_started_before_the_flag_are_not_boundaries(env):
    mt = env['ar']._midturn
    session = _session(_Proc(), _mt_main_tokens=250_000)
    assert mt.note_tool_results(
        session, [{'type': 'tool_result', 'tool_use_id': 'started-before-flag'}]) is False


def test_low_toggling_the_flag_off_and_on_drops_stale_pending_ids(env):
    mt = env['ar']._midturn
    over = env['ar']._context_tokens_over_threshold
    session = _session(_Proc(), _mt_main_tokens=250_000, claude_session_id=CSID)
    mt.note_tool_use(session, _tool_use_block('a'))
    env['CONFIG']['midturn_rollover_enabled'] = False
    mt.note_tool_results(session, [{'type': 'tool_result', 'tool_use_id': 'a'}])  # unseen
    env['CONFIG']['midturn_rollover_enabled'] = True
    assert mt.should_roll(session, over), 'stale id from before the toggle blocked the roll'


def test_n1_interrupting_is_raised_before_the_rearm_runs(env, monkeypatch):
    """The rearm clears the notify sent-latch. If the old reader (another thread
    on the HTTP path) can still deliver a turn-end notify during it, the spawner
    gets the old reply under the new turn and never hears the real one. So the
    flag must already be up when the rearm runs."""
    ar = env['ar']
    seen = []
    monkeypatch.setattr(ar, '_rearm_notify_for_new_turn',
                        lambda s: seen.append(s.get('_interrupting')))
    old = _Proc(pid=1)
    session = _session(old, _mt_main_tokens=250_000)
    env['sessions']['worker-1'] = session
    ar._maybe_midturn_roll(session, old)
    assert seen == [True]
