"""F7 (docs/_review/2026-09-10_security.md) — the incognito promise must
survive a server restart.

Before this fix, incognito status lived ONLY on the in-memory session dict.
After a restart, `_backfill_agent_log_from_transcripts` (server.py) could not
tell an incognito transcript apart from an ordinary orphaned one and
synthesized an agent_log row for it — which fed a Scribe MEMORY.md append,
the "Recent conversations" prompt block, and the cold FTS index. Fixed with a
durable registry (`mc.agent_runtime.mark_transcript_incognito` /
`is_transcript_incognito`, `~/.clayrune/incognito_sessions.json`) that every
transcript reader now keys off, and that fails CLOSED — an unreadable
registry is treated as "everything is incognito", never as "nothing is".

Assertions here drive the REAL functions (backfill, reconcile, FTS build,
the `/api/search/global` route), not reimplementations of them.
"""
import importlib
import json

from mc import agent_runtime


def _server(tmp_data_dir):
    srv = importlib.import_module("server")
    importlib.reload(srv)
    return srv


def _wire_claude_home(monkeypatch, srv, tmp_path):
    """Point every module that scans Claude transcripts at one fake tree —
    mirrors tests/test_memory_fts.py's _wire_claude_home."""
    fake_home = tmp_path / '.claude' / 'projects'
    fake_home.mkdir(parents=True, exist_ok=True)
    import mc.memory as m
    monkeypatch.setattr(m, 'CLAUDE_HOME', fake_home)
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', fake_home)
    return fake_home


def _wire_incognito_home(monkeypatch, tmp_path):
    """Isolate the durable incognito registry from the operator's real
    ~/.clayrune — mirrors tests/test_secrets_store.py's CLAYRUNE_HOME pattern."""
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))


def _write_transcript(path, session_id, turns):
    lines = []
    for role, text in turns:
        if role == 'user':
            lines.append(json.dumps(
                {'type': 'user', 'message': {'role': 'user', 'content': text},
                 'timestamp': '2026-09-10T00:00:00Z'}))
        else:
            lines.append(json.dumps(
                {'type': 'assistant',
                 'message': {'content': [{'type': 'text', 'text': text}]},
                 'timestamp': '2026-09-10T00:00:01Z'}))
    (path / f'{session_id}.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')


# ── link 1 + 3: agent_log backfill (also feeds the prompt-context reader) ───

def test_backfill_ingests_normal_but_skips_incognito_transcript(tmp_data_dir, tmp_path, monkeypatch):
    srv = _server(tmp_data_dir)
    _wire_incognito_home(monkeypatch, tmp_path)
    fake_home = _wire_claude_home(monkeypatch, srv, tmp_path)

    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'proj1', 'project_path': str(project_path)}
    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(str(project_path))
    tdir.mkdir(parents=True)
    _write_transcript(tdir, 'sess-normal', [('user', 'what does the falcon module do')])
    _write_transcript(tdir, 'sess-incog', [('user', 'the off-the-record question')])

    # This is the durable mark a live incognito dispatch writes the moment its
    # csid becomes known (_note_claude_sid, mc/blueprints/agent_routes.py) —
    # simulated directly here since we're not driving a real CLI process.
    agent_runtime.mark_transcript_incognito('sess-incog')

    added = srv._backfill_agent_log_from_transcripts('proj1', project)
    assert added == 1

    log = srv._load_agent_log('proj1')
    csids = {e.get('claude_session_id') for e in log}
    assert 'sess-normal' in csids
    assert 'sess-incog' not in csids


# ── link 2: Scribe reconcile / MEMORY.md — fixed as a CONSEQUENCE of link 1 ──

def test_backfill_and_reconcile_leave_memory_untouched_for_incognito(tmp_data_dir, tmp_path, monkeypatch):
    """Drives the real startup pair (_backfill_all_agent_logs then
    _reconcile_unscribed_sessions) end to end. The incognito transcript never
    gets an agent_log row (previous test), so reconcile — which only ever
    iterates EXISTING agent_log rows — has no opportunity to touch MEMORY.md
    on its account. MEMORY.md must come out byte-for-byte identical."""
    srv = _server(tmp_data_dir)
    _wire_incognito_home(monkeypatch, tmp_path)
    fake_home = _wire_claude_home(monkeypatch, srv, tmp_path)

    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'proj2', 'project_path': str(project_path)}
    srv.save_project('proj2', project)

    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(str(project_path))
    tdir.mkdir(parents=True)
    _write_transcript(tdir, 'sess-normal-2', [('user', 'document the falcon module')])
    _write_transcript(tdir, 'sess-incog-2', [('user', 'an off-the-record question')])
    agent_runtime.mark_transcript_incognito('sess-incog-2')

    mem_path = srv._get_memory_path(project)
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    original = "# Curated\n\n<!-- clayrune:managed:begin -->\n## Session Log\n<!-- clayrune:managed:end -->\n"
    mem_path.write_text(original, encoding='utf-8')

    srv._backfill_all_agent_logs()
    srv._reconcile_unscribed_sessions()

    log = srv._load_agent_log('proj2')
    csids = {e.get('claude_session_id') for e in log}
    assert 'sess-normal-2' in csids
    assert 'sess-incog-2' not in csids

    assert mem_path.read_text(encoding='utf-8') == original


# ── link 4: cold FTS index ───────────────────────────────────────────────────

def test_fts_index_excludes_incognito_includes_normal(tmp_data_dir, tmp_path, monkeypatch):
    srv = _server(tmp_data_dir)
    _wire_incognito_home(monkeypatch, tmp_path)
    fake_home = _wire_claude_home(monkeypatch, srv, tmp_path)
    import mc.memory_fts as fts

    project_path = tmp_path / 'proj'
    project = {'id': 'proj3', 'project_path': str(project_path)}
    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(str(project_path))
    tdir.mkdir(parents=True)
    _write_transcript(tdir, 'sess-normal-3', [
        ('user', 'how does the kestrel widget get built'),
        ('assistant', 'the kestrel widget compiles via the falcon pipeline'),
    ])
    _write_transcript(tdir, 'sess-incog-3', [
        ('user', 'the unspeakable classified secret'),
        ('assistant', 'yes, the unspeakable classified secret is real'),
    ])
    agent_runtime.mark_transcript_incognito('sess-incog-3')

    stats = fts.build_index(project)
    assert stats['files_indexed'] == 1  # only the normal transcript was indexed

    assert len(fts.cold_search(project, 'falcon')) == 1
    assert fts.cold_search(project, 'unspeakable') == []


# ── fail-closed: an unmarkable transcript is not ingested ───────────────────

def test_unreadable_registry_fails_closed_and_blocks_all_ingestion(tmp_data_dir, tmp_path, monkeypatch):
    """If the durable registry exists but can't be parsed, a reader cannot
    tell incognito from ordinary for ANY transcript — so nothing gets
    ingested until the registry is readable again, including a transcript
    that would otherwise have been perfectly normal."""
    srv = _server(tmp_data_dir)
    _wire_incognito_home(monkeypatch, tmp_path)
    fake_home = _wire_claude_home(monkeypatch, srv, tmp_path)

    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'proj4', 'project_path': str(project_path)}
    tdir = fake_home / agent_runtime.ClaudeRuntime._encode_project_path(str(project_path))
    tdir.mkdir(parents=True)
    _write_transcript(tdir, 'sess-normal-4', [('user', 'a perfectly ordinary question')])

    registry_path = tmp_path / '.clayrune' / 'incognito_sessions.json'
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text('{not valid json', encoding='utf-8')

    assert agent_runtime.is_transcript_incognito('sess-normal-4') is True

    added = srv._backfill_agent_log_from_transcripts('proj4', project)
    assert added == 0
    assert srv._load_agent_log('proj4') == []


# ── link 5: the _incognito pseudo-project is unreachable from global search ─

def test_global_search_excludes_incognito_pseudo_project(tmp_data_dir, monkeypatch):
    srv = _server(tmp_data_dir)
    from mc.blueprints import agent_routes as ar

    calls = []

    def fake_search(project, query, limit=50):
        calls.append(project.get('id'))
        return []

    monkeypatch.setattr(ar, '_search_project_transcripts', fake_search)
    monkeypatch.setattr(ar, 'load_projects', lambda: [
        {'id': ar.INCOGNITO_PROJECT_ID, 'project_path': '/wherever/_incognito',
         '_is_incognito_project': True},
        {'id': 'normal-proj', 'project_path': '/wherever/normal-proj'},
    ])

    resp = srv.app.test_client().get('/api/search/global?q=secret')
    assert resp.status_code == 200
    assert ar.INCOGNITO_PROJECT_ID not in calls
    assert 'normal-proj' in calls
