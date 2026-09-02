"""MC-930: session completion must be a runtime-agnostic event.

`_log_agent_completion` was called from exactly two places, and both of them
are the CLAUDE stream readers (`_read_agent_stream`, `_read_agent_stream_b`).
Non-claude providers dispatch through `_dispatch_via_runtime`, whose runtimes
own their own reader threads — so a finished Gemini session wrote NO agent-log
row at all. That starved both downstream fixes at once:

  * MC-929's conversation-rail union reads the agent log, so it had nothing to
    show for these chats — the rail fix was correct but had no input;
  * MC-922's log_lines Scribe fallback is TRIGGERED from the completion path,
    so it never fired and a Gemini session wrote no memory either.

The fix hands the runtime an `on_process_exit` callback that calls the SAME
`_log_agent_completion` — a hook, not a second writer. These tests pin:
a completed non-claude session logs exactly one row per turn, in the shape
`_non_claude_conversation_rows` groups on; the Scribe is triggered with the
session's log_lines available for MC-922's fallback; the claude path is not
double-logged; and the incognito / housekeeping exclusions still hold.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402


class _FakeProc:
    """Just enough of subprocess.Popen for `_mode_a_reader`."""

    def __init__(self, lines, rc=0, pid=424242):
        self.stdout = iter(lines)
        self._rc = rc
        self.pid = pid

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc


class _FakeRuntime:
    """A non-claude runtime that hands its stdout to the REAL `_mode_a_reader`.

    Deliberately drives the shipped generic reader rather than a stand-in, so
    the test breaks if the callback contract in that reader changes.
    """

    name = 'fakeprov'
    display_name = 'FakeProv'

    def __init__(self):
        self.dispatch_callbacks = None

    def model_supported(self, model):
        return False

    def explain_exit_error(self, rc, log_tail):
        return None

    def parse_event(self, line, mc_session_id):
        return None  # plain text — the reader appends it to log_lines

    def run_turn(self, handle, lines, rc=0):
        """Simulate one Mode-A process: spawn, stream, exit."""
        proc = _FakeProc(lines, rc=rc)
        handle.session_dict['proc'] = proc
        handle.session_dict['status'] = 'running'
        handle.session_dict['process_alive'] = True
        agent_runtime_mod._mode_a_reader(proc, handle, self)
        return proc

    def dispatch(self, *, project_path, task, system_prompt='', resume_id='',
                 mode='A', model='', incognito=False, mc_session_id=None,
                 session_dict=None, project_id='', register_process=None,
                 callbacks=None, **_extra):
        self.dispatch_callbacks = callbacks
        return agent_runtime_mod.SessionHandle(
            mc_session_id=mc_session_id,
            provider=self.name,
            mode='A',
            project_path=project_path,
            project_id=project_id,
            session_dict=session_dict,
            started_at=session_dict.get('started_at', ''),
            meta={'callbacks': callbacks or {}},
        )


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """agent_routes wired at tmp_path, a fake non-claude runtime registered,
    and the memory/Scribe fan-out recorded instead of executed."""
    import server  # noqa: F401  (imports + wires the blueprints)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)

    project = {'id': 'proj1', 'project_path': str(tmp_path)}
    monkeypatch.setattr(ar, 'load_project',
                        lambda pid: project if pid == 'proj1' else None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'CTX')

    scribe_calls = []

    def _fake_write_session_memory(p, session, status, summary, ts_date):
        # Record exactly what the real Scribe would receive — MC-922's
        # fallback keys off `log_lines` when there is no claude_session_id.
        scribe_calls.append({
            'status': status,
            'summary': summary,
            'log_lines': list(session.get('log_lines') or []),
            'claude_session_id': session.get('claude_session_id', ''),
            'incognito': session.get('incognito'),
            'housekeeping': session.get('housekeeping'),
        })
        return True

    monkeypatch.setattr(ar, '_write_session_memory', _fake_write_session_memory)

    runtime = _FakeRuntime()
    saved = dict(agent_runtime_mod._RUNTIMES)
    agent_runtime_mod.register_runtime(runtime)

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield {'ar': ar, 'runtime': runtime, 'project': project,
               'scribe_calls': scribe_calls, 'tmp_path': tmp_path,
               'sessions': mc_state.agent_sessions}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)
        agent_runtime_mod._RUNTIMES.clear()
        agent_runtime_mod._RUNTIMES.update(saved)


def _log_rows(env_):
    p = env_['tmp_path'] / 'projects' / 'proj1_agent_log.json'
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding='utf-8'))


def _dispatch(env_, **kw):
    """Real `_dispatch_via_runtime`, then the handle it produced."""
    sid = env_['ar']._dispatch_via_runtime(
        env_['project'], kw.pop('task', 'do the thing'),
        provider_name='fakeprov', **kw)
    handle = agent_runtime_mod.SessionHandle(
        mc_session_id=sid, provider='fakeprov', mode='A',
        project_path=str(env_['tmp_path']), project_id='proj1',
        session_dict=env_['sessions'][sid],
        meta={'callbacks': env_['runtime'].dispatch_callbacks or {}})
    return sid, handle


# ── the regression itself ────────────────────────────────────────────────────

def test_completed_non_claude_session_writes_exactly_one_row(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['Halloway here.', 'Done.'])

    rows = _log_rows(env)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row['session_id'] == sid
    assert row['provider'] == 'fakeprov'
    assert row['claude_session_id'] == ''
    assert row['status'] == 'completed'
    assert row['summary'] == 'Done.'


def test_dispatch_actually_passes_the_completion_hook(env):
    """The wiring, not just the reader: `_dispatch_via_runtime` must hand the
    runtime the shared hook. This is the line that was missing."""
    _dispatch(env)
    cbs = env['runtime'].dispatch_callbacks
    assert cbs, 'runtime.dispatch() got no callbacks'
    assert cbs.get('on_process_exit') is env['ar']._runtime_log_completion


def test_one_row_per_turn_and_mc929_groups_them(env):
    """Mode A respawns per turn; MC-929 groups rows by MC session_id."""
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['turn one output'])
    env['runtime'].run_turn(handle, ['turn two output'])

    rows = _log_rows(env)
    assert len(rows) == 2, rows
    assert {r['session_id'] for r in rows} == {sid}

    convs = env['ar']._non_claude_conversation_rows('proj1', env['project'], 10)
    assert len(convs) == 1, convs
    assert convs[0]['mc_session_id'] == sid
    assert convs[0]['turns'] == 2
    assert convs[0]['provider'] == 'fakeprov'


def test_scribe_is_triggered_with_log_lines_for_mc922_fallback(env):
    """The memory half. The Scribe must be reached, and reached with the
    log_lines MC-922's fallback needs — a non-claude session has no csid."""
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['I read the config and fixed the timeout.'])

    assert len(env['scribe_calls']) == 1, env['scribe_calls']
    call = env['scribe_calls'][0]
    assert call['status'] == 'completed'
    assert call['claude_session_id'] == ''  # forces MC-922's log_lines path
    assert 'I read the config and fixed the timeout.' in call['log_lines']
    # The seeded user prompt is in there too, so the rendered transcript has
    # both sides of the exchange.
    assert any(ln.startswith('> ') for ln in call['log_lines'])


# ── exclusions that must survive ─────────────────────────────────────────────

def test_incognito_non_claude_session_logs_nothing(env):
    sid, handle = _dispatch(env, incognito=True)
    env['runtime'].run_turn(handle, ['secret'])

    assert _log_rows(env) == []
    assert env['scribe_calls'] == []


def test_housekeeping_logs_a_row_but_writes_no_memory(env):
    """Matches `_log_agent_completion` today: housekeeping is excluded from
    MEMORY (circular-trigger guard), NOT from the agent log."""
    sid, handle = _dispatch(env)
    env['sessions'][sid]['housekeeping'] = True
    env['runtime'].run_turn(handle, ['housekeeping output'])

    assert len(_log_rows(env)) == 1
    assert env['scribe_calls'] == []


def test_error_exit_still_logs_and_scribes(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['partial work'], rc=1)

    rows = _log_rows(env)
    assert len(rows) == 1
    assert rows[0]['status'] == 'error'
    assert len(env['scribe_calls']) == 1


def test_replaced_process_does_not_log_a_row(env):
    """Parity with the claude readers' `_session_owned_by` gate: a turn whose
    process was swapped out mid-flight is not logged by the dead reader."""
    sid, handle = _dispatch(env)
    dead = _FakeProc(['stale'], rc=0)
    # A newer process already owns the session.
    env['sessions'][sid]['proc'] = _FakeProc([], rc=0)
    agent_runtime_mod._mode_a_reader(dead, handle, env['runtime'])

    assert _log_rows(env) == []


# ── the claude path must not be double-logged ────────────────────────────────

def test_claude_never_routes_through_the_runtime_hook(env, monkeypatch):
    """The hook is attached only where `_dispatch_via_runtime` builds a handle,
    and claude never reaches that function — so there is no second writer on
    the claude path and no double row."""
    ar = env['ar']
    calls = []
    monkeypatch.setattr(ar, '_dispatch_via_runtime',
                        lambda *a, **k: calls.append(k) or 'sid')
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (None, ''))
    try:
        ar._dispatch_agent_internal('proj1', 'a claude task',
                                    provider_override='claude')
    except Exception:
        pass  # spawning the real CLI is not what this asserts
    assert calls == []


def test_claude_completion_writes_one_row_not_two(env):
    """The claude readers' own call site, unchanged: one process exit, one row."""
    ar = env['ar']
    session = {
        'project_id': 'proj1', 'session_id': 'claudesid01',
        'claude_session_id': 'abc-123', 'task': 'claude task',
        'status': 'completed', 'log_lines': ['> User: hi', 'done'],
        'started_at': '2026-08-31T00:00:00Z', 'provider': 'claude',
    }
    ar._log_agent_completion(session)   # what _read_agent_stream does
    rows = _log_rows(env)
    assert len(rows) == 1, rows
    assert rows[0]['provider'] == 'claude'
