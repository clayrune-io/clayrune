"""MC-935: a failed non-claude run must leave real evidence behind — not the
user's own prompt echoed back as a "result", and not silence on disk.

Backlog item MC-935 was previously marked FIXED; it was not (the referenced
tests didn't exist, and none of the three call sites it names had the claimed
code). Two `market-scout` (gemini) runs failed with `status=error`, `summary`
set to the echoed task, and ZERO lines in `data/logs/clayrune.log`. The Scribe
then read that summary and wrote "no execution data" into MEMORY.md — a silent
failure that reached durable memory looking like an ordinary completion.

Four independent holes, each pinned by a test group below:

1. `_log_agent_completion`'s summary picker took the last log line NOT
   starting with `[` — every marker MC writes IS bracketed, so on an error
   with no real output it fell through to the seed line ("> User: <task>"),
   i.e. the user's own prompt.
2. `_dispatch_via_runtime` (the ONE entry point shared by every non-claude
   provider) never called `_log`, so neither the argv nor a failure ever
   reached `clayrune.log` — only the in-memory session had it, and that gets
   discarded.
3. `_runtime_log_completion` (the shared on_process_exit hook for gemini,
   codex, opencode, goose, aider, kiro) had the same silence: nothing wrote
   an error line to disk on a failed run.
4. `_last_real_error_line` skipped any line that starts AND ends with a
   bracket — which is exactly the shape of `[gemini error] [API Error: ...]`,
   so the one line with the actual cause on it was the one line discarded.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Hole 4 — `_last_real_error_line` and the `[marker] [detail]` shape
# ─────────────────────────────────────────────────────────────────────────────

def test_bracket_wrapped_detail_after_a_marker_is_not_skipped():
    """The exact shape from the incident: `_read_stream` prepends the
    `[gemini error]` marker, and Gemini's own error text is ALSO wrapped in
    brackets. The old `startswith('[') and endswith(']')` check matched the
    WHOLE line and discarded it — the one line with the real cause."""
    tail = ("> Ron: [Competitor watch] check example.com for new pricing\n"
            "[gemini error] [API Error: 500 quota exceeded]\n"
            "[fakeprov exited with code 1]")
    real = agent_runtime_mod._last_real_error_line(tail)
    assert real is not None
    assert 'API Error: 500 quota exceeded' in real


def test_pure_single_bracket_markers_still_skipped():
    """A line that IS one bracket pair start-to-finish — MC's own status
    markers — must still be treated as noise, not surfaced as the error."""
    tail = "[tool: bash]\n[gemini exited with code 1]\n[interrupted]"
    assert agent_runtime_mod._last_real_error_line(tail) is None


def test_real_cli_text_after_markers_still_found():
    """Unbracketed CLI output is unaffected by the marker-detection change."""
    tail = "[tool: bash]\nThe command line is too long.\n[gemini exited with code 1]"
    assert agent_runtime_mod._last_real_error_line(tail) == 'The command line is too long.'


def test_seed_line_still_skipped_by_last_real_error_line():
    """Unchanged behavior (MC-931): the dispatcher's echoed prompt is never
    mistaken for the CLI's error, regardless of the marker-regex change."""
    tail = "> Ron: Reply with exactly one short sentence about the weather"
    assert agent_runtime_mod._last_real_error_line(tail) is None


# ─────────────────────────────────────────────────────────────────────────────
# Fixture shared by holes 1-3 — a fake non-claude runtime through the real
# dispatch + completion plumbing, same shape as test_runtime_completion_log.py
# ─────────────────────────────────────────────────────────────────────────────

class _FakeProc:
    def __init__(self, lines, rc=0, pid=424242):
        self.stdout = iter(lines)
        self._rc = rc
        self.pid = pid

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc


class _FakeRuntime:
    """A non-claude runtime driven through the real `_mode_a_reader` and the
    real `_dispatch_via_runtime` — so these tests break if the shared
    plumbing regresses, not just a hand-rolled stand-in."""

    name = 'fakeprov'
    display_name = 'FakeProv'

    def __init__(self):
        self.dispatch_callbacks = None

    def model_supported(self, model):
        return False

    def explain_exit_error(self, rc, log_tail):
        return None

    def parse_event(self, line, mc_session_id):
        return None  # plain text — the reader appends it to log_lines as-is

    def build_command(self, *, model='', **_extra):
        return ['fakeprov-bin', '--model', model or 'default']

    def run_turn(self, handle, lines, rc=0):
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
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)

    log_calls = []
    monkeypatch.setattr(ar, '_log',
                        lambda *a, **k: log_calls.append(' '.join(str(x) for x in a)))

    runtime = _FakeRuntime()
    saved = dict(agent_runtime_mod._RUNTIMES)
    agent_runtime_mod.register_runtime(runtime)

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield {'ar': ar, 'runtime': runtime, 'project': project,
               'log_calls': log_calls, 'tmp_path': tmp_path,
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
    sid = env_['ar']._dispatch_via_runtime(
        env_['project'], kw.pop('task', 'do the thing'),
        provider_name='fakeprov', **kw)
    handle = agent_runtime_mod.SessionHandle(
        mc_session_id=sid, provider='fakeprov', mode='A',
        project_path=str(env_['tmp_path']), project_id='proj1',
        session_dict=env_['sessions'][sid],
        meta={'callbacks': env_['runtime'].dispatch_callbacks or {}})
    return sid, handle


# ─────────────────────────────────────────────────────────────────────────────
# Hole 2 — a `[dispatch] provider=...` line for every runtime
# ─────────────────────────────────────────────────────────────────────────────

def test_dispatch_writes_a_provider_line_to_disk(env):
    """Before MC-935, nothing on the non-claude dispatch path called `_log`
    at all, so a failed run left zero trace of even having been ATTEMPTED."""
    _dispatch(env, task='[Competitor watch] check example.com')
    assert any(m.startswith('[dispatch] provider=fakeprov') for m in env['log_calls']), \
        env['log_calls']


# ─────────────────────────────────────────────────────────────────────────────
# Hole 3 — a `[runtime-error]` line on the non-claude failure path
# ─────────────────────────────────────────────────────────────────────────────

def test_failed_run_writes_a_runtime_error_line_to_disk(env):
    sid, handle = _dispatch(env, task='[Competitor watch] check example.com')
    env['runtime'].run_turn(
        handle, ['[gemini error] [API Error: 500 quota exceeded]'], rc=1)

    error_lines = [m for m in env['log_calls'] if m.startswith('[runtime-error]')]
    assert error_lines, env['log_calls']
    assert 'provider=fakeprov' in error_lines[0]
    assert 'API Error: 500 quota exceeded' in error_lines[0]


def test_successful_run_writes_no_runtime_error_line(env):
    """The new logging must not fire on the happy path."""
    sid, handle = _dispatch(env, task='do the thing')
    env['runtime'].run_turn(handle, ['all good, done.'], rc=0)

    assert not any(m.startswith('[runtime-error]') for m in env['log_calls'])


# ─────────────────────────────────────────────────────────────────────────────
# Hole 1 — the completion summary can never echo the user's own prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_failed_run_summary_is_not_the_echoed_prompt(env):
    """The incident itself: a run that fails before producing any real output
    must not write the SEEDED PROMPT into `summary` — that is what happened
    on both failed market-scout runs (summary == "> Ron: [Competitor watch]
    ...", the task, verbatim)."""
    task = '[Competitor watch] check example.com for new pricing'
    sid, handle = _dispatch(env, task=task)
    env['runtime'].run_turn(
        handle, ['[gemini error] [API Error: 500 quota exceeded]'], rc=1)

    rows = _log_rows(env)
    assert len(rows) == 1, rows
    summary = rows[0]['summary']
    assert rows[0]['status'] == 'error'
    assert task not in summary
    assert 'Ron' not in summary
    assert not summary.startswith('>')
    # And the real cause is not just dropped — it's what replaces the echo.
    assert 'API Error: 500 quota exceeded' in summary


def test_failed_run_with_truly_no_output_says_so_explicitly(env):
    """A run that errors before printing ANYTHING (no CLI error line either)
    must not fall back to any line in log_lines — say plainly that nothing
    was captured, rather than silently substituting something else."""
    task = 'do something that crashes instantly'
    sid, handle = _dispatch(env, task=task)
    env['runtime'].run_turn(handle, [], rc=1)  # no stdout at all

    rows = _log_rows(env)
    assert len(rows) == 1, rows
    summary = rows[0]['summary']
    assert task not in summary
    assert not summary.startswith('>')
    assert 'no' in summary.lower() or 'no assistant output' in summary.lower()


def test_completed_run_summary_still_picks_real_output(env):
    """Regression guard: the fix must not break the ordinary, working case."""
    sid, handle = _dispatch(env, task='do the thing')
    env['runtime'].run_turn(handle, ['Halloway here.', 'Done.'], rc=0)

    rows = _log_rows(env)
    assert len(rows) == 1
    assert rows[0]['status'] == 'completed'
    assert rows[0]['summary'] == 'Done.'


def test_log_agent_completion_direct_error_no_output(env):
    """Unit-level pin on `_log_agent_completion` itself (not just through the
    dispatch/reader plumbing), for the exact incident shape: log_lines holds
    only the seeded prompt echo plus MC's own bracketed markers."""
    ar = env['ar']
    session = {
        'project_id': 'proj1', 'session_id': 'sid1',
        'claude_session_id': '', 'task': '[Competitor watch] check example.com',
        'status': 'error', 'provider': 'gemini',
        'log_lines': [
            '> Ron: [Competitor watch] check example.com',
            '[gemini exited with code 1]',
        ],
        'started_at': '2026-09-02T00:00:00Z',
    }
    ar._log_agent_completion(session)

    rows = _log_rows(env)
    assert len(rows) == 1
    summary = rows[0]['summary']
    assert '[Competitor watch] check example.com' not in summary
    assert not summary.startswith('>')
