"""MC-1147: a Codex usage-limit failure was reported as rmcp MCP auth noise.

Real incident (2026-11-03, a workflow-dispatched codex run): the agent_log
summary was the rmcp stderr line glued to the front of the partial reply, and
the actual cause -- `usage_limit_exceeded` -- lived only in the rollout's
`task_complete.error`, which was never read after exit. No ALLOWANCE_EXHAUSTED
was recorded either.

Fixtures are the real records, copied from that run's rollout (turn ids
replaced, no account identifiers) and the real stderr line (host genericised):

  tests/fixtures/codex_usage_limit/rollout_tail.jsonl   token_count + task_complete
  tests/fixtures/codex_usage_limit/stderr_tail.txt      the rmcp AuthRequired line

Three root causes, three groups below:
  (a) summary/allowance from the rollout error; stderr is never reply text
  (b) /api/engine/preferences carries a per-provider `allowance`
  (c) rmcp AuthRequired / Transport channel closed lines are WARN, not errors
"""
import json
import sys
import time
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
from mc import allowance_state as al  # noqa: E402

FIXTURES = Path(__file__).parent / 'fixtures' / 'codex_usage_limit'
STDERR_LINE = (FIXTURES / 'stderr_tail.txt').read_text(encoding='utf-8').strip()
THREAD_ID = '01a1a25a-0000-7000-8000-000000000001'
REPLY = 'The gap to test is whether the catalysts change earnings expectations.'


def _fixture_records(now: float):
    """The real rollout records, with completed_at moved to `now` so the
    run-window gate sees them as this run's (the fixture's real timestamps
    are from the incident day)."""
    recs = [json.loads(l) for l in
            (FIXTURES / 'rollout_tail.jsonl').read_text(encoding='utf-8').splitlines() if l]
    for r in recs:
        p = r.get('payload') or {}
        if p.get('type') == 'task_complete':
            p['completed_at'] = int(now) + 1
            p['started_at'] = int(now)
    return recs


def _write_rollout(root: Path, now: float, records=None) -> Path:
    d = root / '2026' / '11' / '03'
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'rollout-2026-11-03T09-00-08-{THREAD_ID}.jsonl'
    f.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n'
                         for r in (records if records is not None else _fixture_records(now))),
                 encoding='utf-8')
    return f


def _stream_lines():
    """What `codex exec --json` + merged stderr delivered for the incident:
    no error event at all, the rmcp stderr line, then a partial reply."""
    return [
        json.dumps({'type': 'thread.started', 'thread_id': THREAD_ID}),
        json.dumps({'type': 'turn.started'}),
        STDERR_LINE,
        json.dumps({'type': 'item.completed',
                    'item': {'id': 'i1', 'type': 'agent_message', 'text': REPLY}}),
    ]


class _FakeProc:
    def __init__(self, lines, rc=0, pid=424242):
        self.stdout = iter(lines)
        self._rc = rc
        self.pid = pid

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc


class _CodexNoopRuntime(agent_runtime_mod.CodexRuntime):
    """The real CodexRuntime (parse_event, transcript_path, explain_exit_error)
    registered under the name the dispatch plumbing expects."""

    dispatch_callbacks = None

    def model_supported(self, model):
        return False

    def dispatch(self, *, project_path, task, system_prompt='', resume_id='',
                 mode='A', model='', incognito=False, mc_session_id=None,
                 session_dict=None, project_id='', register_process=None,
                 callbacks=None, **_extra):
        self.dispatch_callbacks = callbacks
        return agent_runtime_mod.SessionHandle(
            mc_session_id=mc_session_id, provider=self.name, mode='A',
            project_path=project_path, project_id=project_id,
            session_dict=session_dict, started_at=session_dict.get('started_at', ''),
            meta={'callbacks': callbacks or {}})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401  (imports + wires the blueprints)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    project = {'id': 'proj1', 'project_path': str(tmp_path)}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project if pid == 'proj1' else None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'CTX')
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_log', lambda *a, **k: None)

    codex_home = tmp_path / 'codex_sessions'
    monkeypatch.setattr(agent_runtime_mod, '_CODEX_HOME', codex_home)
    al.wire(tmp_path / 'allowance_state.json')

    runtime = _CodexNoopRuntime()
    saved = dict(agent_runtime_mod._RUNTIMES)
    agent_runtime_mod.register_runtime(runtime)
    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield {'ar': ar, 'runtime': runtime, 'project': project, 'tmp_path': tmp_path,
               'codex_home': codex_home, 'sessions': mc_state.agent_sessions}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)
        agent_runtime_mod._RUNTIMES.clear()
        agent_runtime_mod._RUNTIMES.update(saved)
        al._STATE = {}


def _run_incident(env_, *, write_rollout=True, records=None, rc=1):
    """Drive the incident through the real dispatch + `_mode_a_reader` +
    completion plumbing and return the agent_log row."""
    ar = env_['ar']
    sid = ar._dispatch_via_runtime(env_['project'], 'scan the market',
                                   provider_name='codex')
    session = env_['sessions'][sid]
    handle = agent_runtime_mod.SessionHandle(
        mc_session_id=sid, provider='codex', mode='A',
        project_path=str(env_['tmp_path']), project_id='proj1',
        session_dict=session,
        meta={'callbacks': env_['runtime'].dispatch_callbacks or {}})
    if write_rollout:
        _write_rollout(env_['codex_home'], time.time(), records)
    proc = _FakeProc(_stream_lines(), rc=rc)
    session['proc'] = proc
    session['status'] = 'running'
    session['process_alive'] = True
    agent_runtime_mod._mode_a_reader(proc, handle, env_['runtime'])
    p = env_['tmp_path'] / 'projects' / 'proj1_agent_log.json'
    rows = json.loads(p.read_text(encoding='utf-8')) if p.exists() else []
    return session, rows


# ── (a) the incident, end to end ────────────────────────────────────────────

def test_incident_summary_is_the_rollout_error_not_stderr_noise(env):
    session, rows = _run_incident(env)
    assert len(rows) == 1, rows
    summary = rows[0]['summary']
    assert rows[0]['status'] == 'error'
    assert 'rmcp' not in summary and 'AuthRequired' not in summary, summary
    assert summary.startswith('[codex error: usage_limit_exceeded] '), summary
    assert 'usage limit' in summary


def test_incident_records_allowance_exhausted_for_codex(env):
    session, _rows = _run_incident(env)
    rec = al._STATE.get('codex')
    assert rec is not None, al._STATE
    assert rec['limit_kind'] == 'usage_limit'
    assert rec['verified'] is True
    assert session.get('_allowance_exhausted') is True


def test_incident_without_rollout_keeps_old_behaviour_but_not_stderr_as_reply(env):
    """No rollout to read (run died before the CLI wrote one): nothing is
    invented -- but the stderr line must still not be glued onto the reply."""
    session, rows = _run_incident(env, write_rollout=False)
    summary = rows[0]['summary']
    assert 'rmcp' not in summary
    assert REPLY in summary
    assert al._STATE.get('codex') is None


def test_rollout_error_from_an_earlier_turn_is_not_attributed_to_this_run(env):
    """A resumed thread's rollout keeps older turns' task_complete records. If
    this run died before writing its own, the previous turn's error must not
    become this run's summary."""
    recs = _fixture_records(time.time() - 3600)  # an hour before this run
    session, rows = _run_incident(env, records=recs)
    assert '[codex error:' not in rows[0]['summary']
    assert al._STATE.get('codex') is None


def test_clean_exit_does_not_read_the_rollout(env):
    session, rows = _run_incident(env, rc=0)
    assert '[codex error:' not in rows[0]['summary']
    assert al._STATE.get('codex') is None


# ── (a) unit: collector never treats a tracing/stderr line as reply text ────

def test_collector_skips_stderr_line_glued_before_the_reply():
    out = agent_runtime_mod._collect_trailing_reply_text([STDERR_LINE, REPLY])
    assert out == REPLY


def test_collector_skips_stderr_line_trailing_the_reply():
    out = agent_runtime_mod._collect_trailing_reply_text([REPLY, STDERR_LINE])
    assert out == REPLY


def test_collector_returns_empty_when_only_stderr_present():
    assert agent_runtime_mod._collect_trailing_reply_text([STDERR_LINE]) == ''


def test_collector_still_joins_delta_chunks():
    assert agent_runtime_mod._collect_trailing_reply_text(['LIVE', '2-OK']) == 'LIVE2-OK'


def test_plain_text_reply_that_mentions_error_is_still_reply_text():
    line = 'ERROR handling in the parser needs a rewrite.'
    assert agent_runtime_mod._collect_trailing_reply_text([line]) == line


# ── (c) rmcp startup lines are WARN and never the "real error line" ─────────

def test_parse_event_classifies_rmcp_auth_line_as_warn():
    ev = agent_runtime_mod.CodexRuntime().parse_event(STDERR_LINE, 'sid')
    assert ev is not None
    assert ev.type == agent_runtime_mod.EventType.WARN
    assert 'AuthRequired' in ev.payload['text']


def test_parse_event_classifies_transport_closed_without_auth_as_warn():
    line = ('2026-11-03T17:00:08.742449Z ERROR rmcp::transport::worker: '
            'worker quit with fatal: Transport channel closed')
    ev = agent_runtime_mod.CodexRuntime().parse_event(line, 'sid')
    assert ev.type == agent_runtime_mod.EventType.WARN


def test_other_stray_lines_keep_their_old_classification():
    ev = agent_runtime_mod.CodexRuntime().parse_event('Traceback (most recent call last): boom', 'sid')
    assert ev.type == agent_runtime_mod.EventType.ASSISTANT_TEXT


def test_last_real_error_line_skips_rmcp_warning():
    tail = '\n'.join([
        '[codex warn] ' + STDERR_LINE,
        '[codex exited with code 1]',
    ])
    assert agent_runtime_mod._last_real_error_line(tail) is None


def test_last_real_error_line_skips_raw_rmcp_line_and_finds_earlier_real_error():
    tail = '\n'.join(['stream disconnected before completion', STDERR_LINE])
    assert agent_runtime_mod._last_real_error_line(tail) == 'stream disconnected before completion'


def test_reader_logs_rmcp_line_as_a_warn_not_as_reply(env):
    ar = env['ar']
    sid = ar._dispatch_via_runtime(env['project'], 'x', provider_name='codex')
    session = env['sessions'][sid]
    handle = agent_runtime_mod.SessionHandle(
        mc_session_id=sid, provider='codex', mode='A', project_path=str(env['tmp_path']),
        project_id='proj1', session_dict=session,
        meta={'callbacks': env['runtime'].dispatch_callbacks or {}})
    proc = _FakeProc([STDERR_LINE, json.dumps(
        {'type': 'item.completed', 'item': {'id': 'i', 'type': 'agent_message', 'text': 'ok'}})], rc=0)
    session['proc'] = proc
    session['status'] = 'running'
    agent_runtime_mod._mode_a_reader(proc, handle, env['runtime'])
    assert any(l.startswith('[codex warn] ') and 'AuthRequired' in l
               for l in session['log_lines']), session['log_lines']
    assert STDERR_LINE not in session['log_lines']


# ── module unit tests ───────────────────────────────────────────────────────

def test_read_rollout_error_returns_info_and_message(tmp_path):
    from mc import codex_run_error as cre
    now = time.time()
    f = _write_rollout(tmp_path, now)
    err = cre.read_rollout_error(f, since_epoch=now - 5)
    assert err['codex_error_info'] == 'usage_limit_exceeded'
    assert 'usage limit' in err['message']


def test_read_rollout_error_none_when_task_complete_has_no_error(tmp_path):
    from mc import codex_run_error as cre
    now = time.time()
    recs = _fixture_records(now)
    for r in recs:
        if (r.get('payload') or {}).get('type') == 'task_complete':
            r['payload']['error'] = None
    f = _write_rollout(tmp_path, now, recs)
    assert cre.read_rollout_error(f, since_epoch=now - 5) is None


def test_read_rollout_error_none_for_missing_or_unreadable(tmp_path):
    from mc import codex_run_error as cre
    assert cre.read_rollout_error(None, since_epoch=0) is None
    assert cre.read_rollout_error(tmp_path / 'nope.jsonl', since_epoch=0) is None
    bad = tmp_path / 'bad.jsonl'
    bad.write_text('not json\n{"type":\n', encoding='utf-8')
    assert cre.read_rollout_error(bad, since_epoch=0) is None


def test_read_rollout_error_without_codex_error_info_is_ignored(tmp_path):
    from mc import codex_run_error as cre
    now = time.time()
    recs = _fixture_records(now)
    for r in recs:
        if (r.get('payload') or {}).get('type') == 'task_complete':
            r['payload']['error'] = {'message': 'something odd'}
    f = _write_rollout(tmp_path, now, recs)
    assert cre.read_rollout_error(f, since_epoch=now - 5) is None


def test_summary_override_only_for_errored_sessions_with_a_captured_error():
    from mc import codex_run_error as cre
    err = {'codex_error_info': 'usage_limit_exceeded', 'message': 'out'}
    assert cre.summary_override({'status': 'error', '_codex_run_error': err}) == \
        '[codex error: usage_limit_exceeded] out'
    assert cre.summary_override({'status': 'completed', '_codex_run_error': err}) is None
    assert cre.summary_override({'status': 'error'}) is None


# ── (b) /api/engine/preferences `allowance` ─────────────────────────────────

@pytest.fixture()
def client():
    import server
    server.app.config['TESTING'] = True
    return server.app.test_client()


def test_preferences_carries_allowance_from_the_usage_source(client, monkeypatch):
    from mc import provider_allowance
    resets = '2026-11-10T00:00:00+00:00'
    sampled = '2026-11-03T21:30:00+00:00'
    monkeypatch.setattr(provider_allowance, '_sources', lambda: (
        {'seven_day': {'utilization': 61.0, 'resets_at': resets}},
        {'utilization': 5.0, 'resets_at': resets, 'sampled_at': sampled, 'source': 'probe'}))
    al.record_exhaustion('gemini', limit_kind='unknown')
    r = client.get('/api/engine/preferences')
    assert r.status_code == 200
    by_name = {p['name']: p for p in r.get_json()['providers']}
    codex = by_name['codex']['allowance']
    assert codex == {'used_percent': 5.0, 'resets_at': resets, 'sampled_at': sampled,
                     'source': 'probe', 'exhausted_record': None}
    claude = by_name['claude']['allowance']
    assert claude['used_percent'] == 61.0 and claude['resets_at'] == resets
    assert claude['source'] == 'oauth_usage_limits'
    if 'gemini' in by_name:
        g = by_name['gemini']['allowance']
        assert g['used_percent'] is None and g['source'] is None
        assert g['exhausted_record']['limit_kind'] == 'unknown'


def test_allowance_matches_system_usage_for_the_same_inputs(monkeypatch):
    """One assembly function feeds both routes, so they cannot drift."""
    from mc import provider_allowance
    resets = '2026-11-10T00:00:00+00:00'
    claude = {'seven_day': {'utilization': 10.0, 'resets_at': resets}}
    codex = {'utilization': 100.0, 'resets_at': resets, 'sampled_at': 's', 'source': 'rollout'}
    al.record_exhaustion('codex', limit_kind='usage_limit', resets_at='2999-01-01T00:00:00+00:00',
                         resets_at_display='Jan 1st')
    weekly = provider_allowance.build_weekly_usage(claude, codex)
    assert weekly['codex']['exhausted'] is True
    view = provider_allowance.allowance_view('codex', weekly)
    assert view['used_percent'] == 100.0
    assert view['exhausted_record']['limit_kind'] == 'usage_limit'
    # input dict untouched (the usage cache must not leak 'exhausted')
    assert 'exhausted' not in codex


def test_allowance_never_invents_a_percentage():
    from mc import provider_allowance
    view = provider_allowance.allowance_view('codex', {})
    assert view == {'used_percent': None, 'resets_at': None, 'sampled_at': None,
                    'source': None, 'exhausted_record': None}
