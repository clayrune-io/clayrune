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
    # Both lines are one turn's reply with no bracket/seed line between them,
    # so the summary is their full join, not just the last fragment (MC-947 —
    # see `_collect_trailing_reply_text`: a Mode-A provider that logs a reply
    # as several log_lines entries must not have it truncated to the last one).
    assert row['summary'] == 'Halloway here.Done.'


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
    pending = _log_rows(env)
    assert len(pending) == 1 and pending[0]['status'] == 'in_progress'
    dead = _FakeProc(['stale'], rc=0)
    # A newer process already owns the session.
    env['sessions'][sid]['proc'] = _FakeProc([], rc=0)
    agent_runtime_mod._mode_a_reader(dead, handle, env['runtime'])

    assert _log_rows(env) == pending  # stale reader must not complete/update it


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

    def _no_real_cli(*a, **k):  # tests never spawn a real model
        raise RuntimeError('real CLI spawn blocked in test')
    monkeypatch.setattr(ar.subprocess, 'Popen', _no_real_cli)
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


# ── MC-998: completion hook writes a durable usage_breakdown session fact ──

def _breakdown_store(env_):
    from mc.usage_breakdown_store import UsageBreakdownStore
    return UsageBreakdownStore(env_['tmp_path'] / 'usage_breakdown.sqlite')


def test_completion_writes_a_usage_breakdown_session_fact(env):
    """The wiring in `_log_agent_completion_body`, not the sampler's own
    unit-tested logic (that's `tests/test_usage_breakdown_sampler.py`). This
    pins that a real completion, through the real hook, produces a row the
    dashboard can read — the thing the unit tests can't see since they call
    `session_fact_from_entry` directly, never `_log_agent_completion_body`."""
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['did the thing'])

    facts = _breakdown_store(env).list_session_facts()
    assert len(facts) == 1, facts
    assert facts[0]['session_id'] == sid
    assert facts[0]['status'] == 'completed'
    assert facts[0]['provider'] == 'fakeprov'


def test_incognito_session_writes_no_usage_breakdown_fact(env):
    sid, handle = _dispatch(env, incognito=True)
    env['runtime'].run_turn(handle, ['secret'])

    assert _breakdown_store(env).list_session_facts() == []


def test_housekeeping_session_still_writes_a_usage_breakdown_fact(env):
    """Spec §4: housekeeping is a visibility flag on the row, not an
    exclusion — matches the agent-log parity in
    `test_housekeeping_logs_a_row_but_writes_no_memory` above."""
    sid, handle = _dispatch(env)
    env['sessions'][sid]['housekeeping'] = True
    env['runtime'].run_turn(handle, ['housekeeping output'])

    facts = _breakdown_store(env).list_session_facts()
    assert len(facts) == 1
    assert facts[0]['housekeeping'] == 1  # sqlite stores bool as 0/1


def test_error_exit_writes_a_usage_breakdown_fact_with_error_status(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['partial work'], rc=1)

    facts = _breakdown_store(env).list_session_facts()
    assert len(facts) == 1
    assert facts[0]['status'] == 'error'


def test_usage_breakdown_store_failure_does_not_break_completion_logging(env, monkeypatch):
    """Best-effort per the comment at the call site: a store exception must
    not take down the agent-log write it rides alongside."""
    def _boom(self, *a, **kw):
        raise RuntimeError('disk full')
    monkeypatch.setattr(env['ar']._UsageBreakdownStore, 'upsert_session_fact', _boom)

    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['still works'])

    rows = _log_rows(env)
    assert len(rows) == 1
    assert rows[0]['status'] == 'completed'


def test_resumed_turn_reopens_the_usage_breakdown_fact_until_it_completes(env):
    """Round 3 #2 (P1-2, docs/_journal/4668eafc-mc998-fenn-review.md): every
    new turn of a resumed session passes through the dispatch-pending
    writer, which must re-mark the completed fact 'running' -- otherwise
    the fact stays 'completed' at the previous turn's end while this turn
    works, and calibration counts overlapping intervals as single-session.
    The turn's own completion flips it back. A late identity-only INIT
    backfill must NOT reopen it (it can arrive after the turn finished)."""
    ar = env['ar']
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['turn one'])
    store = _breakdown_store(env)
    assert store.get_session_fact(sid)['status'] == 'completed'

    ar._log_agent_dispatch_pending(env['sessions'][sid], identity_only=True)
    assert store.get_session_fact(sid)['status'] == 'completed'

    ar._log_agent_dispatch_pending(env['sessions'][sid])
    assert store.get_session_fact(sid)['status'] == 'running'
    assert _log_rows(env)[0]['status'] == 'in_progress'

    env['runtime'].run_turn(handle, ['turn two'])
    assert store.get_session_fact(sid)['status'] == 'completed'


# ── MC-998 round 4: liveness of the usage-breakdown reconcile ──────────────
# Drives the REAL Mode B reader (`_read_agent_stream_b`), the real
# `_note_self_started_turn`, the real server live set
# (`system_routes._usage_breakdown_live_session_ids`) and the real store. A
# generator-backed process yields a turn's `result`, lets the test run a
# reconcile tick while the process is still alive, then continues.

class _LiveModeBProc:
    """Popen stand-in whose stdout is a generator: `between` runs after
    `before` is consumed (the session is 'idle' after a result line, the
    process still alive), `after_hook` after `after`."""

    pid = -1

    def __init__(self, before, between, after=(), after_hook=None):
        self._before, self._between = before, between
        self._after, self._after_hook = after, after_hook
        self.exited = False

    def poll(self):
        return 0 if self.exited else None

    def wait(self):
        self.exited = True
        return 0

    @property
    def stdout(self):
        def gen():
            yield from self._before
            self._between()
            yield from self._after
            if self._after_hook:
                self._after_hook()
        return gen()


def _mode_b_session(env_, sid):
    from mc.core import TimestampedLines
    s = {'session_id': sid, 'project_id': 'proj1', 'status': 'running', 'mode': 'B',
         'provider': 'claude', 'process_alive': True, 'log_lines': TimestampedLines(),
         'task': 'fixture', 'started_at': '2026-09-28T12:00:00+00:00'}
    env_['sessions'][sid] = s
    return s


def _result_line():
    return json.dumps({'type': 'result', 'usage': {'input_tokens': 100, 'output_tokens': 10}})


def _assistant_line(text):
    return json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}]}})


def _reconcile(env_, **kw):
    from mc.blueprints import system_routes as sr
    from mc.usage_breakdown_sampler import reconcile_dead_sessions
    return reconcile_dead_sessions(_breakdown_store(env_),
                                   live_session_ids=sr._usage_breakdown_live_session_ids, **kw)


def _open_ids(env_):
    return sorted(r['session_id'] for r in _breakdown_store(env_).list_open_sessions())


def _later(**delta):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(**delta)


def test_idle_mode_b_session_with_a_live_process_is_not_closed(env):
    """Fenn round 4 LIVE_mode-b: the reader flips the session to 'idle' on
    every result while the process lives; 1572b2c's status-only live set
    closed it 'ended_unknown'. Checked well past the 120s grace.

    MC-998 reopened: `open` is now `[]`, not `['mb-idle']` -- the per-turn
    checkpoint write (`_write_usage_breakdown_turn_checkpoint`) now stamps a
    terminal 'completed' status with `ended_at` set at the turn's own end
    (Dave's review of the first version of this fix), so `list_open_sessions`
    correctly stops treating the idle-between-turns session as open; the
    next turn's `mark_session_running` reopens it. `closed == []` still
    holds and is what this test actually protects: reconcile has nothing to
    wrongly close here because the store already reflects the turn as done,
    not because it never looked."""
    ar = env['ar']
    seen = {}
    s = _mode_b_session(env, 'mb-idle')
    ar._log_agent_dispatch_pending(s)

    def tick():
        seen['status'] = s['status']
        seen['closed'] = _reconcile(env, now=_later(minutes=30))
        seen['open'] = _open_ids(env)

    proc = _LiveModeBProc([_result_line()], tick)
    s['proc'] = proc
    ar._read_agent_stream_b(proc, s)
    assert seen['status'] == 'idle'
    assert seen['closed'] == []
    assert seen['open'] == []


def test_mc_question_wait_is_not_closed(env):
    """Fenn round 4 LIVE_question: a session parked on an mc:question is
    idle with its process alive -- still live."""
    ar = env['ar']
    seen = {}
    s = _mode_b_session(env, 'mb-question')
    ar._log_agent_dispatch_pending(s)
    text = '```mc:question\n' + json.dumps({'questions': [{
        'header': 'Choice', 'question': 'Which?',
        'options': [{'label': 'A'}, {'label': 'B'}]}]}) + '\n```'

    def tick():
        seen['waiting'] = s.get('waiting_for_question')
        seen['status'] = s['status']
        seen['closed'] = _reconcile(env, now=_later(minutes=30))

    proc = _LiveModeBProc([_assistant_line(text), _result_line()], tick)
    s['proc'] = proc
    ar._read_agent_stream_b(proc, s)
    assert seen['waiting']
    assert seen['status'] == 'idle'
    assert seen['closed'] == []


def test_automatic_wake_marks_the_usage_turn_running(env):
    """Fenn round 4 AUTO_WAKE: a background-task wake starts a turn nobody
    sent. `_note_self_started_turn` must mark the usage fact running on
    EVERY such turn, not only when the INTERIM latch is set -- here it is
    not set, and the fact is stale-'completed' going in.

    MC-998 reopened: no `_before` result line here on purpose. Since the
    turn-boundary fix (`_write_usage_breakdown_turn_checkpoint`) now marks
    the fact 'running' at every ordinary turn's own result too, a `_before`
    result would do this test's job before the wake ever got a chance to —
    collapsing the very distinction ("not only when...") the test exists to
    pin. `status='idle'` is set directly to reproduce the state a prior
    turn's result would have left, without going through that write."""
    ar = env['ar']
    store = _breakdown_store(env)
    seen = {}
    s = _mode_b_session(env, 'mb-wake')
    s['status'] = 'idle'
    ar._log_agent_dispatch_pending(s)
    store.upsert_session_fact('mb-wake', {'provider': 'claude', 'status': 'completed',
                                          'started_at': s['started_at'],
                                          'ended_at': '2026-09-28T12:05:00+00:00',
                                          'token_coverage': 'unavailable'})

    def tick():
        seen['before'] = store.get_session_fact('mb-wake')['status']

    def after_wake():
        seen['memory'] = s['status']
        seen['after'] = store.get_session_fact('mb-wake')['status']

    proc = _LiveModeBProc([], tick,
                          after=[_assistant_line('Background task finished, continuing.')],
                          after_hook=after_wake)
    s['proc'] = proc
    ar._read_agent_stream_b(proc, s)
    assert seen['before'] == 'completed'
    assert seen['memory'] == 'running'
    assert seen['after'] == 'running'


def test_reconcile_reopens_a_wrongly_closed_live_session(env):
    """A fact closed 'ended_unknown' whose session is live after all is
    reopened by the next tick instead of staying closed until exit. With
    the registry unreadable nothing is reopened (or closed)."""
    from mc.usage_breakdown_sampler import reconcile_dead_sessions
    ar = env['ar']
    store = _breakdown_store(env)
    s = _mode_b_session(env, 'mb-reopen')
    s['status'] = 'idle'
    s['proc'] = _LiveModeBProc([], lambda: None)
    ar._log_agent_dispatch_pending(s)
    gen = store.list_open_sessions()[0]['generation']
    assert store.close_session_ended_unknown('mb-reopen', provider='claude', started_at=None,
                                             ended_at='2026-09-28T12:01:00+00:00',
                                             generation=gen)
    assert reconcile_dead_sessions(store, live_session_ids=None) == []
    assert store.get_session_fact('mb-reopen')['status'] == 'ended_unknown'
    assert _reconcile(env, now=_later(minutes=30)) == []
    assert store.get_session_fact('mb-reopen')['status'] == 'running'


def test_pending_launch_is_live_past_the_grace(env):
    """Fenn round 4 PENDING_121: a dispatch whose durable baseline is
    written before Popen is live through state.pending_launches, not only
    through the 120s grace, and leaves it when the launch block exits."""
    from mc import state as mc_state
    ar = env['ar']
    ar._log_agent_dispatch_pending({'project_id': 'proj1', 'session_id': 'pending-1',
                                    'task': 't', 'provider': 'claude'})
    with ar._pending_launch_scope() as scope:
        scope.append('pending-1')
        mc_state.pending_launches['pending-1'] = 0.0
        assert _reconcile(env, now=_later(seconds=121)) == []
    assert 'pending-1' not in mc_state.pending_launches
    assert _reconcile(env, now=_later(seconds=121)) == ['pending-1']


def test_pending_launch_scope_clears_on_a_failed_launch(env):
    from mc import state as mc_state
    ar = env['ar']
    with pytest.raises(OSError):
        with ar._pending_launch_scope() as scope:
            scope.append('pending-2')
            mc_state.pending_launches['pending-2'] = 0.0
            raise OSError('Popen failed')
    assert 'pending-2' not in mc_state.pending_launches


def test_close_loses_to_a_turn_that_started_after_the_snapshot(env):
    """Fenn round 4 SNAPSHOT_RESUME_RACE: the store was read while the
    first-turn session looked dead, then a new turn started. The turn start
    now writes a newer generation (it opens a first turn's fact; bumps an
    existing fact's updated_at), so the stale close is a no-op."""
    from mc.usage_breakdown_sampler import reconcile_dead_sessions
    store = _breakdown_store(env)
    store.record_session_checkpoint(session_id='race', provider='claude',
                                    checkpoint_type='baseline',
                                    observed_at='2026-09-28T11:00:00+00:00')
    snap = store.list_open_sessions()[0]
    assert snap['generation'] is None
    assert store.mark_session_running('race')
    assert not store.close_session_ended_unknown('race', provider='claude', started_at=None,
                                                 ended_at='2026-09-28T12:00:00+00:00',
                                                 generation=snap['generation'])
    snap = store.list_open_sessions()[0]
    store.mark_session_running('race')
    assert not store.close_session_ended_unknown('race', provider='claude', started_at=None,
                                                 ended_at='2026-09-28T12:00:00+00:00',
                                                 generation=snap['generation'])
    assert store.get_session_fact('race')['status'] == 'running'
    # The callable live set is read AFTER the store snapshot inside the
    # reconcile itself: a turn registered in between is seen live.
    assert reconcile_dead_sessions(store, live_session_ids=lambda: {'race'},
                                   now=_later(hours=1)) == []


def test_session_whose_process_exited_without_a_completion_is_closed(env):
    """The crash case still closes: process gone (poll() returns a code),
    status not running, no completion ever written."""
    ar = env['ar']
    s = _mode_b_session(env, 'mb-dead')
    ar._log_agent_dispatch_pending(s)
    s['status'] = 'idle'
    s['proc'] = _FakeProc([], rc=1)
    assert _reconcile(env, now=_later(minutes=5)) == ['mb-dead']
    assert _breakdown_store(env).get_session_fact('mb-dead')['status'] == 'ended_unknown'


# ── MC-998 reopened (backlog 4668eafc, 2026-09-29): Mode B never checkpoints
# a follow-up turn, only real process exit ────────────────────────────────
# `_log_agent_completion` (the only writer of a 'completion' `session_checkpoint`
# before this fix) ran only in `_read_agent_stream_b`'s `finally` block, i.e.
# on real process exit. Mode B's process stays alive across turns, so a live
# chat's 2nd/3rd/... turns got `mark_session_running` (already covered by
# `test_resumed_turn_reopens_the_usage_breakdown_fact_until_it_completes`
# above) but no completion — `_session_evidence` then read every turn after
# the first as one open, unmeasured span forever. This drives the REAL
# `_read_agent_stream_b` across 3 turns without letting the process exit
# between them, and pins a checkpoint after EACH one.

class _MultiTurnModeBProc:
    """Popen stand-in that runs a hook after each stdout line while staying
    'alive' (`poll()` returns None) for the whole sequence — unlike
    `_LiveModeBProc`, which only supports one such hook between two groups
    of lines. Needed here to snapshot state after every one of 3 turns."""

    pid = -1

    def __init__(self, turns):
        self._turns = turns  # [(line, hook_or_None), ...]
        self.exited = False

    def poll(self):
        return None if not self.exited else 0

    def wait(self):
        self.exited = True
        return 0

    @property
    def stdout(self):
        def gen():
            for line, hook in self._turns:
                yield line
                if hook:
                    hook()
        return gen()


def _completion_checkpoints(env_, sid):
    return [c for c in _breakdown_store(env_).list_session_checkpoints()
            if c['session_id'] == sid and c['checkpoint_type'] == 'completion']


def test_mode_b_turn_boundary_writes_a_completion_checkpoint_per_turn(env):
    """Snapshots are taken from INSIDE the stdout generator's hooks, i.e.
    while the simulated process is still running the next turn — never
    after `ar._read_agent_stream_b` returns. Once the generator is
    exhausted the reader's `finally` block sees the process as exited (a
    real Mode B process never would, mid-chat) and runs the unrelated
    exit-time `_log_agent_completion` path, which would add its own
    checkpoint — a 4th row that must not be mistaken for this fix."""
    ar = env['ar']
    seen = {}
    s = _mode_b_session(env, 'mb-multiturn')
    ar._log_agent_dispatch_pending(s)  # baseline, as a real dispatch does
    store = _breakdown_store(env)

    def snapshot(n):
        def _hook():
            seen.setdefault('counts', []).append(len(_completion_checkpoints(env, 'mb-multiturn')))
            fact = store.get_session_fact('mb-multiturn')
            seen.setdefault('status', []).append(fact['status'])
            seen.setdefault('ended_at', []).append(fact['ended_at'])
        return _hook

    proc = _MultiTurnModeBProc([
        (_result_line(), snapshot(1)),
        (_result_line(), snapshot(2)),
        (_result_line(), snapshot(3)),
    ])
    s['proc'] = proc
    ar._read_agent_stream_b(proc, s)

    assert seen['counts'] == [1, 2, 3], seen
    # Status is terminal 'completed' with ended_at stamped at each turn's
    # own end — an open 'running' fact between turns made every window
    # touching a live-but-idle Mode B chat read incomplete (Dave's review
    # of the first version of this fix). The next turn's
    # `_log_agent_dispatch_pending` -> `mark_session_running` reopens it.
    assert seen['status'] == ['completed', 'completed', 'completed'], seen
    assert all(v is not None for v in seen['ended_at']), seen
    # `_accumulate_session_usage` sums each turn's usage into a running
    # session total (Mode B's `result` carries only that turn's own
    # counts) — so each checkpoint's cumulative total climbs, and the
    # aggregate derives turn 2's own delta by diffing checkpoint N-1 from N.
    rows = sorted(_completion_checkpoints(env, 'mb-multiturn'), key=lambda r: r['observed_at'])[:3]
    assert [r['output_tokens'] for r in rows] == [10, 20, 30], rows


def test_mode_b_idle_window_between_turns_is_measurable_not_incomplete(env):
    """Dave's review of the first version of this fix: writing the per-turn
    checkpoint with non-terminal status 'in_progress' left `ended_at` unset,
    so `_session_evidence` (usage_breakdown_aggregate.py) treated everything
    after the LAST completion as one open unmeasured span forever -- a
    window covering only an already-finished turn plus the idle time after
    it still read incomplete, the exact symptom MC-998 exists to fix. The
    terminal 'completed' status (this file's current code) stamps `ended_at`
    at each turn's own end, closing its span there. Fails on the
    'in_progress' version: `incomplete` would be 1, not 0, for `window_a`.

    Range boundaries are the checkpoints' OWN `observed_at` values, read
    from inside each turn's hook (before the harness's end-of-generator
    exit path can append its own artifact completion -- see the sibling
    `test_mode_b_turn_boundary_writes_a_completion_checkpoint_per_turn`'s
    `[:3]` slice for the same caveat) -- never a hook's own wall-clock
    capture, which races the checkpoint write's `load_project` /
    transcript-telemetry read and can land a few hundred ms after it,
    inside the NEXT turn's span, falsely flagging it as crossing."""
    from datetime import timedelta
    from mc.usage_breakdown_aggregate import _parse_iso, filter_facts_in_range
    ar = env['ar']
    s = _mode_b_session(env, 'mb-window')
    ar._log_agent_dispatch_pending(s)
    store = _breakdown_store(env)
    observed = {}

    def snap(n):
        def _hook():
            rows = sorted(_completion_checkpoints(env, 'mb-window'), key=lambda r: r['observed_at'])
            observed[n] = rows[-1]['observed_at']
        return _hook

    proc = _MultiTurnModeBProc([
        (_result_line(), snap(1)),
        (_result_line(), snap(2)),
        (_result_line(), snap(3)),
    ])
    s['proc'] = proc
    ar._read_agent_stream_b(proc, s)

    checkpoints = {'mb-window': store.get_session_checkpoints('mb-window')}
    idle_after = (_parse_iso(observed[3]) + timedelta(hours=1)).isoformat()

    # window_a: turn 1's own end through well past turn 3's end (the idle
    # tail) -- fully contains turns 2 and 3, no open span anywhere inside.
    rows_a, incomplete_a = filter_facts_in_range(
        store.list_session_facts(), checkpoints, provider='claude',
        range_start=observed[1], range_end=idle_after)
    assert incomplete_a == 0, (rows_a, incomplete_a)
    assert rows_a and rows_a[0]['output_tokens'] == 20, rows_a  # turn2 + turn3: 10 + 10

    # A 4th turn starts (mark_session_running) with no completion yet -- a
    # window still reaching into the idle tail must now read incomplete:
    # that turn is open, unmeasured.
    store.mark_session_running('mb-window')
    rows_b, incomplete_b = filter_facts_in_range(
        store.list_session_facts(), checkpoints, provider='claude',
        range_start=observed[1], range_end=idle_after)
    assert incomplete_b == 1, (rows_b, incomplete_b)


# ── Backlog 4668eafc (MC-998 calibration fix): phantom idle-reap checkpoint
# and missing turn_start on internal wakes ─────────────────────────────────

def _turn_start_checkpoints(env_, sid):
    return [c for c in _breakdown_store(env_).list_session_checkpoints()
            if c['session_id'] == sid and c['checkpoint_type'] == 'turn_start']


def test_idle_reap_completion_is_not_double_logged_as_a_checkpoint(env, monkeypatch):
    """Defect 1 (PHANTOM HOUR-LONG TURN): a Mode B process is killed idle
    long after its real last turn completed (eviction, server shutdown, a
    guardian recovery) and `_log_agent_completion` runs a SECOND time from
    the reader thread's `finally` -- same cumulative totals, no turn in
    between. Before the fix this wrote a second 'completion' checkpoint
    stamped at reap time, and `_session_turns` paired it with the real
    completion as an hour-long turn that crossed every calibration interval
    inside the gap (measured live: 82/110 interval checks refused as
    'crossing', 3 sessions with exactly this two-completion, identical-totals
    shape). Reproduces the reap by calling the real completion hook again
    with nothing in the session changed. The phantom guard only fires on a
    REAL matching total (`is not None` on both sides) -- `fakeprov` has no
    transcript for `_session_cumulative_transcript_telemetry` to read, so
    tokens stay None and the guard can never trip; stub a fixed non-zero
    total and relabel the session 'claude' (session_fact_from_entry only
    trusts the stubbed top-level fields for that provider) so this test
    actually exercises the same-totals path instead of vacuously passing."""
    ar = env['ar']

    def _fake_telemetry(project_id, session):
        return {'input_tokens': 500, 'output_tokens': 50}

    monkeypatch.setattr(ar, '_session_cumulative_transcript_telemetry', _fake_telemetry)

    sid, handle = _dispatch(env)
    env['sessions'][sid]['provider'] = 'claude'
    env['runtime'].run_turn(handle, ['did the thing'])
    store = _breakdown_store(env)
    assert len(_completion_checkpoints(env, sid)) == 1

    ar._log_agent_completion(env['sessions'][sid])  # the idle-reap re-fire

    completions = _completion_checkpoints(env, sid)
    assert len(completions) == 1, completions
    fact = store.get_session_fact(sid)
    assert fact['status'] == 'completed'


def test_completion_pair_with_real_delta_is_not_treated_as_phantom(env, monkeypatch):
    """The phantom-reap guard must be narrow: a genuine second turn (new
    tokens, no turn_start row -- old data written before the turn-start fix
    shipped) still produces its own real completion checkpoint, never
    silently dropped alongside the phantom shape above. `fakeprov` has no
    real transcript for `_session_cumulative_transcript_telemetry` to read,
    so it stubs in increasing per-turn totals directly -- the same call the
    real completion path makes to get token counts. `session_fact_from_entry`
    only trusts those top-level fields for `provider == 'claude'`, so the
    session is relabelled post-dispatch (fakeprov still drives the actual
    reader/runtime -- this only affects which token branch the fact builder
    takes)."""
    ar = env['ar']
    telemetry_calls = {'n': 0}

    def _fake_telemetry(project_id, session):
        telemetry_calls['n'] += 1
        n = telemetry_calls['n']
        return {'input_tokens': 100 * n, 'output_tokens': 10 * n}

    monkeypatch.setattr(ar, '_session_cumulative_transcript_telemetry', _fake_telemetry)

    sid, handle = _dispatch(env)
    env['sessions'][sid]['provider'] = 'claude'
    env['runtime'].run_turn(handle, ['turn one'])
    env['runtime'].run_turn(handle, ['turn two, more output this time'])

    completions = _completion_checkpoints(env, sid)
    assert len(completions) == 2, completions
    assert completions[0]['output_tokens'] != completions[1]['output_tokens']


def test_auto_dispatch_followup_writes_a_turn_start_checkpoint(env, monkeypatch):
    """Defect 2 (WAKE TURNS WITH NO turn_start): a queued follow-up drained
    through `_auto_dispatch_followup` (guardian recovery retry, or a stuck
    `pending_followups` queue -- see the Guardian states in agent_routes.py)
    dispatches a fresh turn exactly like agent_followup's own
    `_start_new_turn`, but historically wrote no matching checkpoint --
    `_session_turns` had nothing to pair the new turn against but the
    session's LAST completion, folding the entire queueing delay into the
    turn (measured live: session c11ea2b1, the scheduled backlog runner, had
    5 completions a minute apart with no turn_start and no ticks between any
    of them). Popen and the CLI-flag builders are faked; only the
    checkpoint-write side effect is under test."""
    ar = env['ar']
    s = _mode_b_session(env, 'mb-followup')
    ar._log_agent_dispatch_pending(s)  # baseline
    store = _breakdown_store(env)
    store.upsert_session_fact('mb-followup', {
        'provider': 'claude', 'status': 'completed', 'started_at': s['started_at'],
        'ended_at': '2026-09-30T03:45:00+00:00', 'input_processed_total': 100,
        'output_tokens': 10, 'token_coverage': 'complete'})
    store.record_session_checkpoint(
        session_id='mb-followup', provider='claude', checkpoint_type='completion',
        observed_at='2026-09-30T03:45:00+00:00', input_processed_total=100,
        output_tokens=10, token_coverage='complete')
    assert _turn_start_checkpoints(env, 'mb-followup') == []

    class _FakeFollowupProc:
        pid = 999999

        def poll(self):
            return None

    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'fake')
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *a: ([], None))
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_read_agent_stream', lambda *a, **k: None)
    monkeypatch.setattr(ar.subprocess, 'Popen', lambda *a, **k: _FakeFollowupProc())

    ar._auto_dispatch_followup(s, 'continue please')

    assert s['status'] == 'running'
    turn_starts = _turn_start_checkpoints(env, 'mb-followup')
    assert len(turn_starts) == 1, turn_starts
    assert turn_starts[0]['input_processed_total'] == 100
    assert turn_starts[0]['output_tokens'] == 10
