"""Codex parity fix: `/conversations` must read Codex chats from the REAL
rollout store, not misfile them as transcript-less (docs/research/CODEX_PARITY_AUDIT.md §0).

Before this fix `CodexRuntime.transcript_path()` pointed at a directory layout
that has never existed, so every Codex session looked exactly like Gemini's
"no transcript store" case and fell through to `_non_claude_conversation_rows`
— agent-log rows only, capped at whatever `summary`/`task` happened to hold.
`CodexRuntime.list_sessions()` now scans the real
`~/.codex/sessions/<Y>/<M>/<D>/rollout-<ts>-<thread_id>.jsonl` layout, and
`_recent_codex_conversation_rows` wires it into the union the same way
`_recent_claude_transcripts` feeds the Claude branch.

These tests exercise the fix at the HTTP layer end to end: a real rollout
fixture on disk, an agent-log row carrying the `provider_session_id` that
links the two, and the assertion that the row `/conversations` returns is
sourced from the ROLLOUT (first/last user text, turn count) rather than the
thin agent-log `summary` — proving the transcript path was actually taken,
not just that a row of some shape appeared.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402


def _write_codex_rollout(root, thread_id, cwd, messages, dt='2026-09-07T12-53-28'):
    """Same fixture shape as tests/test_provider_runtimes.py — kept local
    (not imported) since these are two independent test modules and the
    shape is small; both were verified against a live codex-cli 0.153.4
    rollout inspected byte-for-byte."""
    date = dt.split('T')[0]
    y, m, d = date.split('-')
    day_dir = root / y / m / d
    day_dir.mkdir(parents=True, exist_ok=True)
    f = day_dir / f'rollout-{dt}-{thread_id}.jsonl'
    lines = [json.dumps({
        'timestamp': f'{dt}Z', 'ordinal': 0, 'type': 'session_meta',
        'payload': {'session_id': thread_id, 'id': thread_id, 'cwd': cwd,
                    'originator': 'codex_exec', 'cli_version': '0.153.4',
                    'source': 'exec'},
    })]
    for i, (role, text) in enumerate(messages, start=1):
        block_type = 'input_text' if role == 'user' else 'output_text'
        lines.append(json.dumps({
            'timestamp': f'{dt}Z', 'ordinal': i, 'type': 'response_item',
            'payload': {'type': 'message', 'id': f'item_{i}', 'role': role,
                        'content': [{'type': block_type, 'text': text}]},
        }))
    f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return f


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Same shape as test_conversation_union.py's client fixture, plus the
    Codex rollout store repointed at a tmp dir so real files can be written
    without touching the operator's actual ~/.codex/sessions."""
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    project_path = tmp_path / 'project'
    project_path.mkdir()
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': pid, 'project_path': str(project_path)} if pid == 'proj1' else None))
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda project_path, limit=10, must_include_csids=None: [])
    monkeypatch.setattr(agent_runtime_mod, '_CODEX_HOME', tmp_path / 'codex_sessions')
    # The read-through caches are module-level and keyed by absolute path —
    # a prior test in the same process could leave a stale entry that
    # happens to collide on (mtime, size). Clear both so each test starts
    # cold against its own tmp_path.
    agent_runtime_mod._CODEX_META_CACHE.clear()
    agent_runtime_mod._CODEX_ROW_CACHE.clear()

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client(), project_path
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


def test_external_codex_transcript_viewer_without_mc_id(client, tmp_path):
    http, project_path = client
    native_id = '01a07d6e-cc73-7011-bb97-f2b08136fb87'
    _write_codex_rollout(tmp_path / 'codex_sessions', native_id, str(project_path),
                         [('user', 'external question'), ('assistant', 'saved answer')])
    response = http.get(f'/api/project/proj1/transcript/{native_id}?provider=codex')
    assert response.status_code == 200
    data = response.get_json()
    assert data['provider'] == 'codex'
    assert data['messages'] == [{'role': 'user', 'text': 'external question'},
                                {'role': 'assistant', 'text': 'saved answer'}]
    assert 'session_id' not in data
    assert http.get(f'/api/project/proj1/transcript/{native_id}?provider=bogus').status_code == 400
    assert http.get('/api/project/proj1/transcript/*?provider=codex').status_code == 400


def test_external_codex_viewer_rejects_other_project(client, tmp_path):
    http, _ = client
    native_id = '01a07d6e-cc73-7011-bb97-f2b08136fb87'
    _write_codex_rollout(tmp_path / 'codex_sessions', native_id, str(tmp_path / 'other'),
                         [('user', 'private question')])
    assert http.get(f'/api/project/proj1/transcript/{native_id}?provider=codex').status_code == 404


def test_native_history_send_resumes_exact_codex_id(client, tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar
    http, project_path = client
    native_id = '01a07d6e-cc73-7011-bb97-f2b08136fb87'
    _write_codex_rollout(tmp_path / 'codex_sessions', native_id, str(project_path),
                         [('user', 'old question'), ('assistant', 'old answer')])
    calls = []
    def dispatch(pid, message, **kwargs):
        calls.append((pid, message, kwargs))
        ar.agent_sessions['owned'] = {'project_id': pid, 'provider': 'codex',
            'provider_session_id': native_id, 'status': 'idle', 'log_lines': ['new prompt']}
        return 'owned'
    monkeypatch.setattr(ar, '_dispatch_agent_internal', dispatch)
    monkeypatch.setattr(ar, 'agent_followup', lambda pid: ar.jsonify({'ok': True, 'session_id': 'owned'}))
    body = {'session_id': 'codex:proj1:' + native_id, 'provider': 'codex',
            'provider_session_id': native_id, 'message': 'continue'}
    response = http.post('/api/project/proj1/agent/send', json=body)
    assert response.status_code == 200
    assert response.get_json()['route'] == 'resume-native'
    assert calls[0][2]['resume_id'] == native_id
    assert calls[0][2]['provider_override'] == 'codex'
    assert ar.agent_sessions['owned']['log_lines'][1] == 'old answer'
    response = http.post('/api/project/proj1/agent/send', json=body)
    assert response.status_code == 200
    assert len(calls) == 1  # subsequent sends reuse the existing owner
    body['provider_session_id'] = '00000000-0000-0000-0000-000000000000'
    assert http.post('/api/project/proj1/agent/send', json=body).status_code == 404
    assert len(calls) == 1  # missing history must never dispatch fresh


def _write_log(tmp_path, project_id, entries):
    (tmp_path / 'projects' / f'{project_id}_agent_log.json').write_text(
        json.dumps(entries), encoding='utf-8')


def _codex_entry(session_id, provider_session_id, ts, task, summary):
    return {
        'ts': ts, 'task': task, 'status': 'completed', 'summary': summary,
        'session_id': session_id, 'claude_session_id': '',
        'provider_session_id': provider_session_id, 'started_at': ts,
        'trigger_type': 'manual', 'source': 'ui', 'provider': 'codex',
        'character': None, 'num_turns': 1,
    }


def test_codex_conversation_appears_sourced_from_the_real_rollout(client, tmp_path):
    http, project_path = client
    thread_id = '01a07d6e-cc73-7011-bb97-f2b08136fb87'
    _write_codex_rollout(
        tmp_path / 'codex_sessions', thread_id, str(project_path),
        [('user', 'diagnose the failing build'),
         ('assistant', 'found it — stale lockfile'),
         ('user', 'fix it'),
         ('assistant', 'pushed the fix')])
    # The agent-log summary is deliberately a short/generic stand-in — the
    # assertion below must come from the ROLLOUT, not this field, or the test
    # would pass even if list_sessions() were never called.
    _write_log(tmp_path, 'proj1', [
        _codex_entry('mcsid-codex-1', thread_id, '2026-09-07T12-00-00Z',
                     'diagnose the failing build', '[turn 2 of 2]'),
    ])

    resp = http.get('/api/project/proj1/conversations')
    assert resp.status_code == 200
    rows = resp.get_json()
    codex_rows = [r for r in rows if r.get('provider') == 'codex']
    assert len(codex_rows) == 1, rows
    row = codex_rows[0]
    assert row['mc_session_id'] == 'mcsid-codex-1'
    assert row['provider_session_id'] == thread_id
    assert row['turns'] == 2, row               # 2 user turns in the rollout
    assert row['first_user'] == 'diagnose the failing build'
    assert row['last_user'] == 'fix it'         # rollout text, not the log summary
    # 2026-09-16: resume is wired end-to-end (CodexRuntime.dispatch() already
    # threaded resume_id into `exec resume <thread_id>`; the gap was only that
    # `_revive_non_claude_from_agent_log` never passed it through). A row with
    # a real mc_session_id can resolve provider_session_id from the agent log
    # server-side at follow-up time, so it is honestly resumable now.
    assert row['resumable'] is True
    assert row['resume_mode'] == 'live'
    assert row['status'] == 'completed'


def test_codex_conversation_not_double_listed_via_non_claude_fallback(client, tmp_path):
    """The row must come from EXACTLY one of the two sources, not both —
    _non_claude_conversation_rows must exclude an mc_session_id that the
    rollout-backed path already covered."""
    http, project_path = client
    thread_id = 'thread-dedup-check'
    _write_codex_rollout(tmp_path / 'codex_sessions', thread_id, str(project_path),
                         [('user', 'hello'), ('assistant', 'hi there')])
    _write_log(tmp_path, 'proj1', [
        _codex_entry('mcsid-codex-2', thread_id, '2026-09-07T12-00-00Z', 'hello', 'hi there'),
    ])
    rows = http.get('/api/project/proj1/conversations').get_json()
    matches = [r for r in rows if r['mc_session_id'] == 'mcsid-codex-2']
    assert len(matches) == 1, rows


def test_pre_fix_codex_row_without_provider_session_id_still_falls_back(client, tmp_path):
    """An agent-log entry written before this fix (or a turn whose INIT
    capture failed) carries no provider_session_id — must still show up via
    the old agent-log-only path rather than vanishing."""
    http, project_path = client
    entry = _codex_entry('mcsid-codex-legacy', '', '2026-09-07T12-00-00Z',
                         'legacy task', 'legacy summary')
    del entry['provider_session_id']  # pre-fix shape: key absent entirely
    _write_log(tmp_path, 'proj1', [entry])

    rows = http.get('/api/project/proj1/conversations').get_json()
    matches = [r for r in rows if r['mc_session_id'] == 'mcsid-codex-legacy']
    assert len(matches) == 1, rows
    assert matches[0]['label'] == 'legacy summary'


def test_codex_session_from_a_different_project_is_not_leaked(client, tmp_path):
    """list_sessions() scans ALL rollouts on the machine (Codex has no
    per-project directory) — cwd scoping must actually filter, not just
    decorate the query. No agent-log row for proj1 at all here: the rollout
    belongs entirely to a different project's chat history."""
    http, project_path = client
    other_project = tmp_path / 'other_project'
    other_project.mkdir()
    _write_codex_rollout(tmp_path / 'codex_sessions', 'thread-other-project',
                         str(other_project), [('user', 'unrelated project chat')])
    rows = http.get('/api/project/proj1/conversations').get_json()
    assert rows == []


def test_live_codex_session_status_join(client, tmp_path):
    """A running Codex session's live status/waiting flags must join onto the
    rollout-derived row by provider_session_id, same as Claude joins by csid."""
    http, project_path = client
    from mc import state as mc_state

    thread_id = 'thread-live'
    _write_codex_rollout(tmp_path / 'codex_sessions', thread_id, str(project_path),
                         [('user', 'still going')])
    mc_state.agent_sessions['mcsid-live'] = {
        'project_id': 'proj1', 'session_id': 'mcsid-live',
        'provider_session_id': thread_id, 'status': 'running',
        'provider': 'codex', 'task': 'still going',
        'log_lines': ['> still going'], 'started_at': '2026-09-07T12:00:00Z',
        'waiting_for_question': True,
    }
    rows = http.get('/api/project/proj1/conversations').get_json()
    codex_rows = [r for r in rows if r.get('provider') == 'codex']
    assert len(codex_rows) == 1, rows
    assert codex_rows[0]['live'] is True
    assert codex_rows[0]['status'] == 'running'
    assert codex_rows[0]['waiting_for_question'] is True
    status_rows = http.get('/api/project/proj1/agent/status').get_json()['sessions']
    assert status_rows[0]['provider_session_id'] == thread_id
