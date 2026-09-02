"""mc/memory_fts.py — cold session search (MC-918).

Follows the test_memory_module.py convention: reload server against
tmp_data_dir so mc.memory.wire() binds to an isolated dir, then drive
mc.memory_fts directly. CLAUDE_HOME is repointed on BOTH mc.memory (which
mc.memory_fts._db_path reaches via _get_memory_path) and agent_runtime (which
mc.memory_fts._iter_transcript_files reads directly) to the SAME tmp dir —
mirroring how server.py wires both from one real path in production.
"""
import importlib
import json

from mc import agent_runtime


def _mem(tmp_data_dir):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    import mc.memory as m
    return m


def _wire_claude_home(monkeypatch, m, tmp_path):
    fake_home = tmp_path / '.claude' / 'projects'
    fake_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(m, 'CLAUDE_HOME', fake_home)
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', fake_home)
    return fake_home


def _write_transcript(path, session_id, turns):
    """turns: [(role, text), ...] -> a minimal Claude Code JSONL transcript."""
    lines = []
    for role, text in turns:
        if role == 'user':
            lines.append(json.dumps(
                {'type': 'user', 'message': {'role': 'user', 'content': text},
                 'timestamp': '2026-08-01T00:00:00Z'}))
        else:
            lines.append(json.dumps(
                {'type': 'assistant',
                 'message': {'content': [{'type': 'text', 'text': text}]},
                 'timestamp': '2026-08-01T00:00:01Z'}))
    (path / f'{session_id}.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _project(tmp_path, pid='ftsproj'):
    return {'id': pid, 'project_path': str(tmp_path / 'proj')}


# ── indexing ──────────────────────────────────────────────────────────────

def test_build_index_indexes_user_and_assistant_text(tmp_data_dir, tmp_path, monkeypatch):
    m = _mem(tmp_data_dir)
    fake_home = _wire_claude_home(monkeypatch, m, tmp_path)
    import mc.memory_fts as fts
    p = _project(tmp_path)
    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(p['project_path'])
    tdir.mkdir(parents=True)
    _write_transcript(tdir, 'sess-a', [
        ('user', 'How does the kestrel widget get built'),
        ('assistant', 'The kestrel widget compiles via the falcon pipeline'),
    ])

    stats = fts.build_index(p)
    assert stats['files_indexed'] == 1
    assert stats['rows_written'] == 2

    hits = fts.cold_search(p, 'falcon')
    assert len(hits) == 1
    hit = hits[0]
    assert hit['tier'] == 'cold'
    assert hit['session_id'] == 'sess-a'
    assert hit['role'] == 'assistant'
    assert hit['timestamp']
    assert 'falcon' in hit['snippet'].lower()


def test_build_index_indexes_a_dispatched_subagents_own_transcript(tmp_data_dir, tmp_path, monkeypatch):
    """MC-941: a Task-tool subagent's messages live ONLY in its own nested
    transcript (`<encoded>/<parent_csid>/subagents/agent-<id>.jsonl`), never
    inlined into the parent session's `<csid>.jsonl`. This module's whole
    premise (module docstring: "everything a project has ever actually said
    lives in its session transcripts") is broken if that nested file is
    invisible to the index — a `prd-writer` subagent's findings would be
    unsearchable even though the top-level scan ran against the right
    project. Mirrors test_documents_tab.py's
    test_finds_a_write_from_a_dispatched_subagents_own_transcript.
    """
    m = _mem(tmp_data_dir)
    fake_home = _wire_claude_home(monkeypatch, m, tmp_path)
    import mc.memory_fts as fts
    p = _project(tmp_path)
    parent_dir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(p['project_path'])
    parent_dir.mkdir(parents=True)
    _write_transcript(parent_dir, 'parent-csid', [('user', 'dispatch a spec writer')])
    sub_dir = parent_dir / 'parent-csid' / 'subagents'
    sub_dir.mkdir(parents=True)
    _write_transcript(sub_dir, 'agent-abc123', [
        ('assistant', 'the kestrel widget compiles via the falcon pipeline'),
    ])

    stats = fts.build_index(p)
    assert stats['files_seen'] == 2
    assert stats['files_indexed'] == 2

    hits = fts.cold_search(p, 'falcon')
    assert len(hits) == 1
    assert hits[0]['session_id'] == 'agent-abc123'


def test_build_index_skips_tool_calls_and_short_text(tmp_data_dir, tmp_path, monkeypatch):
    m = _mem(tmp_data_dir)
    fake_home = _wire_claude_home(monkeypatch, m, tmp_path)
    import mc.memory_fts as fts
    p = _project(tmp_path)
    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(p['project_path'])
    tdir.mkdir(parents=True)
    lines = [
        json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'ok'}}),
        json.dumps({'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Bash', 'input': {}}]}}),
    ]
    (tdir / 'sess-b.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    stats = fts.build_index(p)
    assert stats['files_indexed'] == 1
    assert stats['rows_written'] == 0


def test_build_index_is_incremental_on_unchanged_file(tmp_data_dir, tmp_path, monkeypatch):
    """Second call over an unchanged transcript skips it entirely — the whole
    point of the fingerprint table (MC-918's 'do not re-index every boot')."""
    m = _mem(tmp_data_dir)
    fake_home = _wire_claude_home(monkeypatch, m, tmp_path)
    import mc.memory_fts as fts
    p = _project(tmp_path)
    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(p['project_path'])
    tdir.mkdir(parents=True)
    _write_transcript(tdir, 'sess-c', [('user', 'the falcon pipeline question')])

    first = fts.build_index(p)
    assert first['files_indexed'] == 1
    second = fts.build_index(p)
    assert second['files_indexed'] == 0
    assert second['rows_written'] == 0


def test_build_index_reindexes_on_append_without_duplicating(tmp_data_dir, tmp_path, monkeypatch):
    m = _mem(tmp_data_dir)
    fake_home = _wire_claude_home(monkeypatch, m, tmp_path)
    import mc.memory_fts as fts
    p = _project(tmp_path)
    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(p['project_path'])
    tdir.mkdir(parents=True)
    _write_transcript(tdir, 'sess-d', [('user', 'the osprey migration plan')])
    fts.build_index(p)

    _write_transcript(tdir, 'sess-d', [
        ('user', 'the osprey migration plan'),
        ('assistant', 'the osprey migration is complete now'),
    ])
    second = fts.build_index(p)
    assert second['files_indexed'] == 1
    assert second['rows_written'] == 2

    hits = fts.cold_search(p, 'osprey', limit=10)
    assert len(hits) == 2, 'stale rows from the pre-append file must be replaced, not duplicated'


def test_cold_search_returns_empty_before_any_index_built(tmp_data_dir, tmp_path, monkeypatch):
    m = _mem(tmp_data_dir)
    _wire_claude_home(monkeypatch, m, tmp_path)
    import mc.memory_fts as fts
    p = _project(tmp_path)
    assert fts.cold_search(p, 'anything') == []


def test_session_fts_enabled_false_disables_build_and_search(tmp_data_dir, tmp_path, monkeypatch):
    m = _mem(tmp_data_dir)
    fake_home = _wire_claude_home(monkeypatch, m, tmp_path)
    import mc.memory_fts as fts
    from mc import state
    p = _project(tmp_path)
    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(p['project_path'])
    tdir.mkdir(parents=True)
    _write_transcript(tdir, 'sess-e', [('user', 'the gate should never fire')])

    monkeypatch.setitem(state.CONFIG, 'session_fts_enabled', False)
    stats = fts.build_index(p)
    assert stats == {'files_seen': 0, 'files_indexed': 0, 'rows_written': 0,
                     'agent_log_indexed': False, 'elapsed_s': 0.0}
    assert fts.cold_search(p, 'gate') == []


def test_agent_log_task_and_summary_are_indexed(tmp_data_dir, tmp_path, monkeypatch):
    m = _mem(tmp_data_dir)
    _wire_claude_home(monkeypatch, m, tmp_path)
    import mc.memory_fts as fts
    p = {'id': 'ftslog'}  # no project_path -> falls back to MEMORY_DIR/<id>.md
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    log = [{
        'task': 'investigate the heron regression in the pipeline',
        'summary': 'root cause was a heron off-by-one, fixed in commit abc123',
        'session_id': 'agentlog-sess-1',
        'claude_session_id': 'csid-1',
        'started_at': '2026-08-05T00:00:00Z',
    }]
    (data_dir / 'ftslog_agent_log.json').write_text(json.dumps(log), encoding='utf-8')

    stats = fts.build_index(p, data_dir=data_dir)
    assert stats['agent_log_indexed'] is True
    assert stats['rows_written'] == 2

    hits = fts.cold_search(p, 'heron', limit=10)
    assert len(hits) == 2
    assert {h['role'] for h in hits} == {'task', 'summary'}
    assert all(h['session_id'] == 'csid-1' for h in hits)


# ── route-level: /api/project/<id>/memory/search cold tier ─────────────────

def test_route_appends_cold_hits_after_index_hits(tmp_data_dir, tmp_path, monkeypatch):
    """The cold tier is additive: existing index-tier hits pass through
    unmodified (backward compat for existing callers), cold hits are appended
    and self-label via tier='cold'."""
    from mc.blueprints import guide_routes as gr

    index_hits = [{'file': 'topic.md', 'score': 3, 'snippet': 'hit for condense'}]
    monkeypatch.setattr(gr, '_memory_search', lambda p, q, k, expand=0: list(index_hits))
    cold_hits = [{'tier': 'cold', 'file': 'session:abc', 'session_id': 'abc',
                  'role': 'user', 'timestamp': 't', 'source': 's',
                  'snippet': 'a cold hit', 'score': -1.0}]
    monkeypatch.setattr(gr._mem_fts, 'cold_search', lambda p, q, limit=5: list(cold_hits))
    monkeypatch.setattr(gr, 'load_project', lambda pid: {'id': pid} if pid == 'r' else None)

    import server  # noqa: F401 — app already built by _mem() above
    client = server.app.test_client()
    r = client.get('/api/project/r/memory/search?q=condense&k=5')
    assert r.status_code == 200
    body = r.get_json()
    assert body == index_hits + cold_hits


def test_route_cold_k_zero_opts_out(tmp_data_dir, tmp_path, monkeypatch):
    from mc.blueprints import guide_routes as gr
    index_hits = [{'file': 'topic.md', 'score': 1, 'snippet': 'x'}]
    monkeypatch.setattr(gr, '_memory_search', lambda p, q, k, expand=0: list(index_hits))
    called = []
    monkeypatch.setattr(gr._mem_fts, 'cold_search',
                        lambda p, q, limit=5: called.append(limit) or [])
    monkeypatch.setattr(gr, 'load_project', lambda pid: {'id': pid} if pid == 'r' else None)

    import server
    client = server.app.test_client()
    r = client.get('/api/project/r/memory/search?q=x&cold_k=0')
    assert r.status_code == 200
    assert r.get_json() == index_hits
    assert called == []
