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
        self.last_resume_id = None

    def model_supported(self, model):
        return False

    def explain_exit_error(self, rc, log_tail):
        return None

    def parse_event(self, line, mc_session_id):
        # A magic line lets a test simulate the provider's INIT event (what
        # CodexRuntime.parse_event does for `thread.started`) without needing
        # a second fake-runtime class — _mode_a_reader's INIT branch is what
        # writes session['provider_session_id'].
        if line.startswith('__INIT__:'):
            tid = line.split(':', 1)[1]
            return agent_runtime_mod.AgentEvent(
                type=agent_runtime_mod.EventType.INIT, provider=self.name,
                session_id=tid, mc_session_id=mc_session_id,
                timestamp='', payload={'session_id': tid, 'thread_id': tid})
        # __MSG__: yields a REAL ASSISTANT_TEXT event (unlike the default
        # None below, which the reader appends as a raw non-protocol line
        # without ever feeding turn_text_parts/mc:-block detection) — needed
        # to drive the mc:question/mc:todo scan at turn end, which only sees
        # text that arrived as an ASSISTANT_TEXT event.
        if line.startswith('__MSG__:'):
            text = line.split(':', 1)[1]
            return agent_runtime_mod.AgentEvent(
                type=agent_runtime_mod.EventType.ASSISTANT_TEXT, provider=self.name,
                session_id=None, mc_session_id=mc_session_id,
                timestamp='', payload={'text': text})
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
        self.last_resume_id = resume_id
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


# ── provider_session_id: captured, and now persisted (parity audit §0/item 2) ─
# Previously written in exactly one place (_mode_a_reader's INIT branch) and
# read in zero — it never reached the agent log, so a Codex conversation had
# no id to find its rollout BY once the in-memory session was gone. This pins
# the wiring through the SAME real _mode_a_reader -> _log_agent_completion
# path the row-count tests above exercise, not a hand-built entry dict.

def test_provider_session_id_reaches_the_agent_log(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['__INIT__:thread-abc-123', 'the reply'])

    assert handle.session_dict['provider_session_id'] == 'thread-abc-123'
    rows = _log_rows(env)
    assert len(rows) == 1, rows
    assert rows[0]['provider_session_id'] == 'thread-abc-123'


def test_provider_session_id_absent_defaults_to_empty_string(env):
    """A provider that never emits an INIT event (or a turn before it fires)
    must not KeyError _log_agent_completion — the field is opt-in per turn."""
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['no init event this turn'])

    rows = _log_rows(env)
    assert len(rows) == 1
    assert rows[0]['provider_session_id'] == ''


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


# ── MC-934 quota key mismatch (parity audit item 3a) ────────────────────────
# `_runtime_log_completion` used to read `session['model']`, a field ONLY the
# claude respawn path ever sets (pinned respawn, agent_send). Every non-claude
# session stamps its model into `agent_model` (_dispatch_via_runtime) instead,
# so `[runtime-error] ... model= ...` was always written blank, and
# `_recent_quota_failures` silently drops any line with an empty model —
# quota protection was a no-op for every runtime-dispatched provider.

def test_error_line_carries_agent_model_when_model_field_is_unset(env, monkeypatch):
    ar = env['ar']
    lines = []
    monkeypatch.setattr(ar, '_log', lambda msg, **k: lines.append(msg))

    sid, handle = _dispatch(env, model_override='gpt-5-codex')
    env['runtime'].run_turn(handle, ['boom'], rc=1)

    error_lines = [ln for ln in lines if ln.startswith('[runtime-error]')]
    assert len(error_lines) == 1, lines
    assert 'model=gpt-5-codex' in error_lines[0]


def test_error_line_prefers_explicit_model_field_over_agent_model(env, monkeypatch):
    """A claude-style pinned respawn (session['model'] set) must not be
    shadowed by a stale agent_model — model wins when both are present."""
    ar = env['ar']
    lines = []
    monkeypatch.setattr(ar, '_log', lambda msg, **k: lines.append(msg))

    sid, handle = _dispatch(env, model_override='gpt-5-codex')
    env['sessions'][sid]['model'] = 'gpt-5-codex-mini'
    env['runtime'].run_turn(handle, ['boom'], rc=1)

    error_lines = [ln for ln in lines if ln.startswith('[runtime-error]')]
    assert len(error_lines) == 1, lines
    assert 'model=gpt-5-codex-mini' in error_lines[0]


# ── Resume threading (parity audit item 3, "Resume") ────────────────────────
# `_dispatch_via_runtime` hardcoded `resume_id=''`, so `CodexRuntime.
# build_command`'s correctly-built `exec resume <id>` branch was unreachable —
# every "resumed" Codex chat was actually a fresh thread wearing the old MC
# session_id. These pin the id actually reaching runtime.dispatch(), and the
# session dict carrying it forward before any INIT event has fired.

def test_resume_id_reaches_runtime_dispatch(env):
    sid, handle = _dispatch(env, resume_id='thread-prior-99')
    assert env['runtime'].last_resume_id == 'thread-prior-99'


def test_resume_id_seeds_provider_session_id_before_init_fires(env):
    """If the process errors before emitting thread.started, the session
    must still carry the id it was resuming — not go blank."""
    sid, handle = _dispatch(env, resume_id='thread-prior-99')
    assert env['sessions'][sid]['provider_session_id'] == 'thread-prior-99'


def test_the_seeded_resume_id_survives_the_turns_init_event(env):
    """`_mode_a_reader`'s INIT branch uses setdefault (agent_runtime.py) so a
    turn's own thread.started never clobbers an identity already known for
    this MC session — which is what keeps a resumed chat's provider_session_id
    stable across every subsequent respawn, not just the first one."""
    sid, handle = _dispatch(env, resume_id='thread-prior-99')
    env['runtime'].run_turn(handle, ['__INIT__:thread-prior-99', 'ok'])
    assert env['sessions'][sid]['provider_session_id'] == 'thread-prior-99'


def test_fresh_dispatch_without_resume_id_is_unaffected(env):
    sid, handle = _dispatch(env)
    assert env['runtime'].last_resume_id == ''
    assert 'provider_session_id' not in env['sessions'][sid]


def test_prior_character_matches_by_provider_session_id(env):
    """A non-claude resume has no claude_session_id to key off — the persona
    lookup that `_dispatch_agent_internal` uses ahead of the provider branch
    must also recognize `provider_session_id`."""
    ar = env['ar']
    dave = {'scope': 'global', 'name': 'dave'}
    monkeypatch_log = [{'provider_session_id': 'thread-prior-99',
                        'claude_session_id': '', 'character': dave}]
    orig = ar._load_agent_log
    ar._load_agent_log = lambda pid: monkeypatch_log
    try:
        assert ar._prior_character('proj1', 'thread-prior-99') == 'global:dave'
    finally:
        ar._load_agent_log = orig


# ── mc:question on the shared Mode-A reader (parity audit item 4) ──────────
# The universal context block tells every Mode-A provider to use the mc:
# fence, but `_mode_a_reader` never scanned for it — the fence landed in
# log_lines as literal text and the run dead-ended. These drive the REAL
# `_mode_a_reader` (via `_FakeRuntime.run_turn`, same as every test above),
# not a mock, so they break if the wiring is removed or the ordering of the
# status-decision vs mc-tool-scan changes.

def test_mc_question_pauses_the_turn_and_populates_pending_questions(env):
    sid, handle = _dispatch(env)
    lines = [
        '__MSG__:Let me check something first.\n',
        '__MSG__:```mc:question\n',
        ('__MSG__:{"questions": [{"header": "Env", "question": "Which env?", '
         '"options": [{"label": "prod", "description": "production"}, '
         '{"label": "dev", "description": "development"}]}]}\n'),
        '__MSG__:```\n',
    ]
    env['runtime'].run_turn(handle, lines, rc=0)
    session = env['sessions'][sid]
    assert session['status'] == 'idle'
    assert session['waiting_for_question'] is True
    assert len(session['pending_questions']) == 1
    q = session['pending_questions'][0]['questions'][0]
    assert q['question'] == 'Which env?'
    assert q['options'][0]['label'] == 'prod'
    # The raw fence must never reach the visible chat.
    assert not any('```mc:question' in ln for ln in session['log_lines'])
    assert any('Let me check something first.' in ln for ln in session['log_lines'])
    assert any('Waiting for your answer' in ln for ln in session['log_lines'])
    # The Mode-A PROCESS still exited (it's a respawn-per-turn provider), so
    # the completion hook still fires and logs a row — just tagged 'idle',
    # not 'completed'/'error', matching what the reply's later write_followup
    # respawn will overwrite once it resolves.
    rows = _log_rows(env)
    assert len(rows) == 1
    assert rows[0]['status'] == 'idle'


def test_mc_question_malformed_block_is_reported_not_silently_dropped(env):
    sid, handle = _dispatch(env)
    lines = [
        '__MSG__:```mc:question\n',
        '__MSG__:{not valid json\n',
        '__MSG__:```\n',
    ]
    env['runtime'].run_turn(handle, lines, rc=0)
    session = env['sessions'][sid]
    assert session.get('waiting_for_question') is not True
    assert session['status'] == 'completed'  # rc=0, no valid question to pause on
    assert any('malformed block' in ln for ln in session['log_lines'])


def test_ordinary_text_with_no_mc_fence_is_unaffected(env):
    """The common case — no mc: fence at all — must render identically to
    plain ASSISTANT_TEXT with no suppression or scanning artifacts."""
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['__MSG__:Reading the config now.',
                                     '__MSG__:Done, config looks fine.'], rc=0)
    session = env['sessions'][sid]
    assert session['status'] == 'completed'
    assert 'Reading the config now.' in session['log_lines']
    assert 'Done, config looks fine.' in session['log_lines']
    assert not any('Waiting for your answer' in ln for ln in session['log_lines'])
