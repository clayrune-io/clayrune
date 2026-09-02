"""The Documents tab (MC-939): mc/blueprints/agent_routes.py + agent_runtime.py.

The PLANS tab used to be scoped to exactly one directory (_is_plan_path,
~/.claude/plans/) — a spec an agent wrote to docs/ under the project itself
could never appear, even though it's exactly the kind of artifact ("written
for a human to read") the tab exists for.

This is a MERGE, not a replacement:
  - plans (~/.claude/plans/*.md) keep working exactly as before, 'kind':'plan'
  - agent-written markdown is DERIVED from the project's own Claude
    transcripts (Write/Edit tool_use on a *.md target) via
    ClaudeRuntime.list_written_markdown(), 'kind':'doc'

Three properties under test:
  1. list_written_markdown() finds a doc from a real (synthetic) transcript
     and per-file caches it by (mtime, size) — an unchanged transcript is not
     re-parsed.
  2. get_project_documents() merges both sources, containment-checks 'doc'
     rows to the project's own tree (unlike serve-file's cross-project
     reach), and marks 'doc' rows non-deletable.
  3. /api/document-file broadens /api/plan-file's plans-only gate to the
     project's own root + uploads + plans dir — and still refuses anything
     outside that, and secret-looking files.
"""
import json
import sys
import threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _write_transcript(path, blocks_by_line):
    """Write a minimal Claude-CLI-shaped .jsonl transcript.

    blocks_by_line: list of (timestamp, [block, ...]) — one 'assistant' line
    per entry, mirroring what ClaudeRuntime.parse_event() expects.
    """
    with open(path, 'w', encoding='utf-8') as fh:
        for ts, blocks in blocks_by_line:
            line = {
                'type': 'assistant',
                'session_id': path.stem,
                'timestamp': ts,
                'message': {'content': blocks},
            }
            fh.write(json.dumps(line) + '\n')


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    import mc.agent_runtime as art
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    plans_dir = tmp_path / 'plans'
    plans_dir.mkdir()
    monkeypatch.setattr(ar, '_PLANS_DIR', plans_dir)

    uploads_dir = tmp_path / 'uploads'
    uploads_dir.mkdir()
    monkeypatch.setattr(ar, 'UPLOADS_DIR', uploads_dir)

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))

    project_path = tmp_path / 'repo'
    project_path.mkdir()
    (project_path / 'docs').mkdir()
    (data_dir / 'p1.json').write_text(
        json.dumps({'id': 'p1', 'name': 'P1', 'backlog': [],
                    'project_path': str(project_path)}), encoding='utf-8')

    claude_home = tmp_path / 'claude_projects'
    claude_home.mkdir()
    monkeypatch.setattr(art, '_CLAUDE_HOME', claude_home)
    # Per-transcript doc-write cache is process-global — a stale entry from an
    # earlier test (same tmp path reused across a run) would hide a real
    # rewrite. Each test gets a clean cache.
    monkeypatch.setattr(art, '_DOC_WRITE_CACHE', {})

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    c = types.SimpleNamespace(
        ar=ar, art=art, plans=plans_dir, uploads=uploads_dir,
        project_path=project_path, claude_home=claude_home,
        sessions=mc_state.agent_sessions, client=server.app.test_client(),
        data_dir=data_dir)
    yield c

    mc_state.agent_sessions.clear()
    mc_state.agent_sessions.update(snapshot)


def _plan(env, name='design.md'):
    p = env.plans / name
    p.write_text(f'# {name}\n', encoding='utf-8')
    return str(p)


def _transcript_dir_for(env):
    encoded = env.art.ClaudeRuntime._encode_project_path(str(env.project_path))
    d = env.claude_home / encoded
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seed_written_doc(env, session_id='csid1', rel='docs/SPEC.md',
                      content='# Spec\n\nbody', ts='2026-09-01T00:00:00Z'):
    """Write a synthetic transcript recording a Write tool_use for `rel`
    (under the fixture project) and actually create that file on disk, the
    way a real agent turn would."""
    target = env.project_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    d = _transcript_dir_for(env)
    _write_transcript(d / f'{session_id}.jsonl', [
        (ts, [{'type': 'tool_use', 'name': 'Write', 'id': 't1',
              'input': {'file_path': str(target), 'content': content}}]),
    ])
    return str(target)


# ── ClaudeRuntime.list_written_markdown ──────────────────────────────────────

def test_finds_a_write_tool_call_targeting_markdown(env):
    target = _seed_written_doc(env)
    hits = env.art.get_runtime('claude').list_written_markdown(str(env.project_path))
    assert [h['path'] for h in hits] == [target]
    assert hits[0]['tool'] == 'Write'
    assert hits[0]['session_id'] == 'csid1'


def test_non_markdown_writes_are_ignored(env):
    target = env.project_path / 'notes.txt'
    target.write_text('hi', encoding='utf-8')
    d = _transcript_dir_for(env)
    _write_transcript(d / 'csid2.jsonl', [
        ('2026-09-01T00:00:00Z', [{'type': 'tool_use', 'name': 'Write', 'id': 't1',
                                   'input': {'file_path': str(target)}}]),
    ])
    hits = env.art.get_runtime('claude').list_written_markdown(str(env.project_path))
    assert hits == []


def test_an_unchanged_transcript_is_not_reparsed(env, monkeypatch):
    _seed_written_doc(env)
    rt = env.art.get_runtime('claude')
    first = rt.list_written_markdown(str(env.project_path))
    assert len(first) == 1

    # If the cache didn't hold, a second call would call parse_event() again;
    # break parse_event and prove the cached path is what actually ran.
    def _boom(*a, **k):
        raise AssertionError('parse_event called on an unchanged transcript')
    monkeypatch.setattr(rt, 'parse_event', _boom)
    second = rt.list_written_markdown(str(env.project_path))
    assert second == first


def test_finds_a_write_from_a_dispatched_subagents_own_transcript(env):
    """The real gap found verifying MC-939 against docs/CHANNEL_MODEL_SPEC.md:
    a Task-tool subagent's Write call lives ONLY in its own nested transcript
    (`<encoded>/<parent_csid>/subagents/agent-<id>.jsonl`), never inlined into
    the parent session's own `<csid>.jsonl` (which carries just the `Agent`
    tool's prompt/result). A flat `*.jsonl` glob of the encoded dir misses it
    entirely — this is what `d.glob('*/subagents/**/*.jsonl')` is for.
    """
    target = env.project_path / 'docs' / 'SPEC_FROM_SUBAGENT.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('# Subagent spec', encoding='utf-8')

    encoded = env.art.ClaudeRuntime._encode_project_path(str(env.project_path))
    parent_dir = env.claude_home / encoded
    parent_dir.mkdir(parents=True, exist_ok=True)
    # Parent session's own transcript: only the Agent tool_use, no Write.
    _write_transcript(parent_dir / 'parent-csid.jsonl', [
        ('2026-09-02T00:00:00Z', [{'type': 'tool_use', 'name': 'Agent', 'id': 'a1',
                                   'input': {'description': 'Write a spec',
                                             'subagent_type': 'prd-writer'}}]),
    ])
    sub_dir = parent_dir / 'parent-csid' / 'subagents'
    sub_dir.mkdir(parents=True)
    _write_transcript(sub_dir / 'agent-abc123.jsonl', [
        ('2026-09-02T00:05:00Z', [{'type': 'tool_use', 'name': 'Write', 'id': 't1',
                                   'input': {'file_path': str(target), 'content': '# Subagent spec'}}]),
    ])

    hits = env.art.get_runtime('claude').list_written_markdown(str(env.project_path))
    assert str(target) in [h['path'] for h in hits]

    got = env.client.get('/api/project/p1/documents').get_json()
    assert str(target) in [d['path'] for d in got]


def test_workflow_fanout_subagent_transcript_is_also_found(env):
    """Same nested shape one level deeper — CC's Workflow feature (fan-out),
    which _scan_project_workflows already reads at
    `.../subagents/workflows/<wf_id>/journal.jsonl`. Its per-agent transcripts
    sit alongside that journal as `agent-<id>.jsonl`."""
    target = env.project_path / 'docs' / 'FANOUT.md'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('# Fanout', encoding='utf-8')

    encoded = env.art.ClaudeRuntime._encode_project_path(str(env.project_path))
    wf_dir = env.claude_home / encoded / 'parent2' / 'subagents' / 'workflows' / 'wf1'
    wf_dir.mkdir(parents=True)
    _write_transcript(wf_dir / 'agent-01.jsonl', [
        ('2026-09-02T00:00:00Z', [{'type': 'tool_use', 'name': 'Write', 'id': 't1',
                                   'input': {'file_path': str(target)}}]),
    ])
    hits = env.art.get_runtime('claude').list_written_markdown(str(env.project_path))
    assert str(target) in [h['path'] for h in hits]


def test_edit_after_write_updates_the_cached_hit(env):
    target = _seed_written_doc(env, ts='2026-09-01T00:00:00Z')
    d = _transcript_dir_for(env)
    # Append an Edit line for the same file, same transcript — changes the
    # file's mtime/size, so the cache must re-parse and pick up both hits,
    # keeping the LATEST by timestamp.
    with open(d / 'csid1.jsonl', 'a', encoding='utf-8') as fh:
        fh.write(json.dumps({
            'type': 'assistant', 'session_id': 'csid1',
            'timestamp': '2026-09-01T01:00:00Z',
            'message': {'content': [{'type': 'tool_use', 'name': 'Edit', 'id': 't2',
                                     'input': {'file_path': target}}]},
        }) + '\n')
    hits = env.art.get_runtime('claude').list_written_markdown(str(env.project_path))
    assert len(hits) == 1
    assert hits[0]['tool'] == 'Edit'
    assert hits[0]['ts'] == '2026-09-01T01:00:00Z'


# ── /api/project/<id>/documents ───────────────────────────────────────────────

def test_a_plan_and_a_written_doc_both_appear(env):
    plan = _plan(env, 'a.md')
    doc = _seed_written_doc(env)
    env.sessions['s1'] = {'session_id': 's1', 'project_id': 'p1', 'task': 't',
                          'started_at': '2026-09-01T00:00:00Z',
                          'plan_files': [plan]}
    got = env.client.get('/api/project/p1/documents').get_json()
    by_kind = {d['kind']: d for d in got}
    assert by_kind['plan']['path'] == plan
    assert by_kind['plan']['deletable'] is True
    assert by_kind['doc']['path'] == doc
    assert by_kind['doc']['deletable'] is False
    assert by_kind['doc']['location'] == 'docs\\SPEC.md' or by_kind['doc']['location'] == 'docs/SPEC.md'


def test_a_doc_outside_the_project_tree_does_not_leak_in(env):
    outside = env.claude_home.parent / 'elsewhere.md'
    outside.write_text('# nope', encoding='utf-8')
    d = _transcript_dir_for(env)
    _write_transcript(d / 'csid3.jsonl', [
        ('2026-09-01T00:00:00Z', [{'type': 'tool_use', 'name': 'Write', 'id': 't1',
                                   'input': {'file_path': str(outside)}}]),
    ])
    got = env.client.get('/api/project/p1/documents').get_json()
    assert got == []


def test_a_doc_written_then_deleted_is_not_listed(env):
    doc = _seed_written_doc(env)
    Path(doc).unlink()
    got = env.client.get('/api/project/p1/documents').get_json()
    assert got == []


def test_unknown_project_404s(env):
    res = env.client.get('/api/project/nope/documents')
    assert res.status_code == 404


# ── /api/document-file ───────────────────────────────────────────────────────

def test_reads_a_plan_file(env):
    plan = _plan(env, 'a.md')
    res = env.client.get(f'/api/document-file?path={plan}')
    assert res.status_code == 200
    assert res.get_json()['content'] == '# a.md\n'


def test_reads_a_doc_under_the_projects_own_root(env):
    doc = _seed_written_doc(env, content='# Spec\n\nreal content')
    res = env.client.get(f'/api/document-file?path={doc}&project_id=p1')
    assert res.status_code == 200
    assert 'real content' in res.get_json()['content']


def test_refuses_a_path_outside_every_allowed_root(env, tmp_path):
    rogue = tmp_path / 'rogue.md'
    rogue.write_text('# rogue', encoding='utf-8')
    res = env.client.get(f'/api/document-file?path={rogue}&project_id=p1')
    assert res.status_code == 403


def test_refuses_a_secret_looking_file_even_inside_the_project(env):
    # .md extension gate means a real secret file (.env, .pem) can't reach
    # this route via extension alone — but a markdown file with a
    # credential-ish name inside the project root must still be refused,
    # since read_document_file() applies the same _is_secret_file() check
    # /api/serve-file uses.
    secret = env.project_path / 'credentials.md'
    secret.write_text('# shh', encoding='utf-8')
    res = env.client.get(f'/api/document-file?path={secret}&project_id=p1')
    assert res.status_code == 403


def test_refuses_a_non_markdown_extension(env):
    other = env.project_path / 'notes.txt'
    other.write_text('hi', encoding='utf-8')
    res = env.client.get(f'/api/document-file?path={other}&project_id=p1')
    assert res.status_code == 415


def test_missing_path_is_a_400(env):
    res = env.client.get('/api/document-file')
    assert res.status_code == 400
