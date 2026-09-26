"""MC-944 step 7 (b2d85e51, §6.5 Conditions 21-23) — the CALLER wiring.

Follow-up to test_memory_v2_step7.py, which covers the primitives
(mint_topic_node, scan_docs_artifacts_for_mint, unresolved_mint_block,
resolve_mint) in isolation. Dave's review of the first pass found every one
of those primitives had ZERO callers outside mc/memory.py — flag ON still
minted nothing, because nothing ever called them. This file proves the five
call sites actually fire, and that each still no-ops with the flag OFF:

  1. hivemind close        -> mc/blueprints/hivemind_routes.py orchestrator loop
  2. backlog item -> done  -> mc/blueprints/project_routes.py PATCH route
  3. docs/ artifact scan   -> mc/memory.py _write_session_memory (session end)
  4. unresolved-mint block -> mc/blueprints/agent_routes.py _build_agent_context
  5. RESOLVE route         -> mc/blueprints/guide_routes.py resolve_mint_route
"""
import json
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _mint_files(tmp_path):
    return sorted(tmp_path.glob('mint_*.md'))


# ── 1. hivemind close ────────────────────────────────────────────────────────

@pytest.fixture()
def hm_env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc import memory as mem
    from mc.blueprints import hivemind_routes as hm

    hm_dir = tmp_path / 'hiveminds'
    hm_dir.mkdir()
    monkeypatch.setattr(hm, 'HIVEMIND_DIR', hm_dir)

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    project = {'id': 'p1', 'project_path': str(proj_path)}
    monkeypatch.setattr(hm, 'load_project', lambda pid: project if pid == 'p1' else None)
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')

    hid = 'hm_test1'
    (hm_dir / hid / 'workstreams').mkdir(parents=True)
    manifest = {'id': hid, 'project_id': 'p1', 'title': 'Ship the thing',
                'goal': 'Ship the thing end to end', 'status': 'active',
                'config': {}}
    (hm_dir / hid / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    ws = {'id': 'ws1', 'status': 'completed', 'dependencies': []}
    (hm_dir / hid / 'workstreams' / 'ws1.json').write_text(json.dumps(ws), encoding='utf-8')

    # Stop the loop after exactly one pass: dispatch_orchestrator (called for
    # a 'completed' outcome, right before the mint wiring) sets the stop
    # event as a side effect. The current iteration keeps running to
    # completion — only the *next* `while` check sees it — so the mint call
    # right after it still executes deterministically, with no sleep/timing.
    monkeypatch.setattr(hm, '_hm_dispatch_orchestrator',
                         lambda *a, **k: hm._hivemind_orchestrator_stop.set())
    hm._hivemind_orchestrator_stop.clear()

    yield hm, mem, mc_state, tmp_path, hid
    hm._hivemind_orchestrator_stop.clear()


def test_hivemind_close_mints_when_flag_on(hm_env):
    hm, mem, mc_state, tmp_path, hid = hm_env
    mc_state.CONFIG['memory_mint_triggers_enabled'] = True
    try:
        hm._hivemind_orchestrator_loop()
    finally:
        mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    minted = _mint_files(tmp_path)
    assert len(minted) == 1
    text = minted[0].read_text(encoding='utf-8')
    assert 'origin: unattended' in text
    manifest = json.loads((tmp_path / 'hiveminds' / hid / 'manifest.json').read_text())
    assert manifest['status'] == 'completed'


def test_hivemind_close_noop_when_flag_off(hm_env):
    hm, mem, mc_state, tmp_path, hid = hm_env
    mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)  # default OFF
    hm._hivemind_orchestrator_loop()
    assert _mint_files(tmp_path) == []
    manifest = json.loads((tmp_path / 'hiveminds' / hid / 'manifest.json').read_text())
    assert manifest['status'] == 'completed'  # outcome logic itself is unaffected


# ── 2. backlog item -> done ──────────────────────────────────────────────────

@pytest.fixture()
def backlog_env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc import memory as mem
    from mc.blueprints import project_routes as pr
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    project = {'id': 'p1', 'project_path': str(proj_path),
               'backlog': [{'id': 'b1', 'text': 'Ship the thing', 'status': 'open'}]}
    projects = {'p1': project}
    monkeypatch.setattr(pr, 'load_project', lambda pid: projects.get(pid))
    saved = []
    monkeypatch.setattr(pr, 'save_project', lambda pid, p: saved.append((pid, p)))

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    yield c, mem, mc_state, tmp_path


def test_backlog_done_mints_when_flag_on(backlog_env):
    c, mem, mc_state, tmp_path = backlog_env
    mc_state.CONFIG['memory_mint_triggers_enabled'] = True
    try:
        resp = c.patch('/api/project/p1/backlog/b1', json={'status': 'done'})
    finally:
        mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    assert resp.status_code == 200
    minted = _mint_files(tmp_path)
    assert len(minted) == 1
    assert 'origin: unattended' in minted[0].read_text(encoding='utf-8')


def test_backlog_done_noop_when_flag_off(backlog_env):
    c, mem, mc_state, tmp_path = backlog_env
    mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    resp = c.patch('/api/project/p1/backlog/b1', json={'status': 'done'})
    assert resp.status_code == 200
    assert _mint_files(tmp_path) == []


def test_backlog_wontdo_never_mints(backlog_env):
    """wontdo is a CLOSED status but not 'done' — a declined item is a
    position, not new ground truth worth a topic node (§6.5)."""
    c, mem, mc_state, tmp_path = backlog_env
    mc_state.CONFIG['memory_mint_triggers_enabled'] = True
    try:
        resp = c.patch('/api/project/p1/backlog/b1', json={'status': 'wontdo'})
    finally:
        mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    assert resp.status_code == 200
    assert _mint_files(tmp_path) == []


def test_backlog_done_replay_does_not_remint(backlog_env):
    """PATCHing status=done twice (idempotent client retry) must not mint
    twice — record_backlog_status_change's own no-op-on-unchanged-status
    return value gates the second call."""
    c, mem, mc_state, tmp_path = backlog_env
    mc_state.CONFIG['memory_mint_triggers_enabled'] = True
    try:
        c.patch('/api/project/p1/backlog/b1', json={'status': 'done'})
        c.patch('/api/project/p1/backlog/b1', json={'status': 'done'})
    finally:
        mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    assert len(_mint_files(tmp_path)) == 1


# ── 3. docs/ artifact scan at session end ───────────────────────────────────

@pytest.fixture()
def session_end_env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc import memory as mem

    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(mem, '_scribe_extract', lambda p, session: (None, 'disabled'))
    monkeypatch.setattr(mem, '_commit_managed_entry', lambda *a, **k: False)
    monkeypatch.setattr(mem, '_dispatch_condense', lambda p: None)
    monkeypatch.setattr(mem._distiller, '_distill_extract_and_aggregate', lambda *a, **k: None)

    proj_path = tmp_path / 'proj'
    docs_dir = proj_path / 'docs'
    docs_dir.mkdir(parents=True)
    big = docs_dir / 'BIG_SPEC.md'
    big.write_text('x' * 5000, encoding='utf-8')
    import os
    import time as _t
    now = _t.time()
    os.utime(big, (now, now))
    project = {'id': 'p1', 'project_path': str(proj_path)}

    yield mem, mc_state, tmp_path, project, now


def test_session_end_scans_docs_when_flag_on(session_end_env):
    mem, mc_state, tmp_path, project, now = session_end_env
    mc_state.CONFIG['memory_mint_triggers_enabled'] = True
    session = {'session_id': 's1', 'task': 'build the spec', 'trigger_type': 'manual',
               'started_at': mem.now_iso() if False else None, 'log_lines': []}
    # started_at must predate the artifact's mtime; build one a minute earlier.
    from datetime import datetime, timedelta, timezone
    session['started_at'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    try:
        mem._write_session_memory(project, session, 'completed', '', '2026-09-26')
    finally:
        mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    minted = _mint_files(tmp_path)
    assert len(minted) == 1
    assert 'origin: interactive' in minted[0].read_text(encoding='utf-8')


def test_session_end_noop_when_flag_off(session_end_env):
    mem, mc_state, tmp_path, project, now = session_end_env
    mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    from datetime import datetime, timedelta, timezone
    session = {'session_id': 's1', 'task': 'build the spec', 'trigger_type': 'manual',
               'started_at': (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
               'log_lines': []}
    mem._write_session_memory(project, session, 'completed', '', '2026-09-26')
    assert _mint_files(tmp_path) == []


# ── 4. unresolved-mint block in ATTENDED-turn context ───────────────────────

@pytest.fixture()
def ctx_env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc import memory as mem
    from mc.blueprints import agent_routes as ar

    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    tmp_path.mkdir(exist_ok=True)
    from mc.memory import write_topic_note
    mc_state.CONFIG['memory_mint_triggers_enabled'] = True
    fn = write_topic_note(
        {'id': 'p1'}, 'mint_hivemind_close_deadbeef01', 'Hivemind closed: old thing',
        '**Trigger:** hivemind_close\n\nold thing', note_type='project',
        supersedes='unresolved', mint_candidates='some_other_note')
    assert fn, 'fixture setup: seed mint must actually write'

    project = {'id': 'p1', 'project_path': str(tmp_path)}
    yield ar, mc_state, project


def _sid_for(monkeypatch, trigger_type):
    from mc.state import agent_sessions
    sid = 'sess1'
    agent_sessions[sid] = {'session_id': sid, 'trigger_type': trigger_type}
    return sid


def test_unresolved_mint_block_appears_on_attended_turn(ctx_env, monkeypatch):
    ar, mc_state, project = ctx_env
    sid = _sid_for(monkeypatch, 'manual')
    try:
        ctx = ar._build_agent_context(project, incognito=False, task='continue the work',
                                       session_id=sid)
    finally:
        from mc.state import agent_sessions
        agent_sessions.pop(sid, None)
    assert 'MEMORY MINT NEEDS ONE WORD' in ctx
    assert '/memory/mints/' in ctx


def test_unresolved_mint_block_absent_on_unattended_turn(ctx_env, monkeypatch):
    ar, mc_state, project = ctx_env
    sid = _sid_for(monkeypatch, 'schedule')
    try:
        ctx = ar._build_agent_context(project, incognito=False, task='continue the work',
                                       session_id=sid)
    finally:
        from mc.state import agent_sessions
        agent_sessions.pop(sid, None)
    assert 'MEMORY MINT NEEDS ONE WORD' not in ctx


def test_unresolved_mint_block_absent_when_incognito(ctx_env, monkeypatch):
    ar, mc_state, project = ctx_env
    sid = _sid_for(monkeypatch, 'manual')
    try:
        ctx = ar._build_agent_context(project, incognito=True, task='continue the work',
                                       session_id=sid)
    finally:
        from mc.state import agent_sessions
        agent_sessions.pop(sid, None)
    assert 'MEMORY MINT NEEDS ONE WORD' not in ctx


def test_unresolved_mint_block_absent_when_flag_off(ctx_env, monkeypatch):
    ar, mc_state, project = ctx_env
    mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    sid = _sid_for(monkeypatch, 'manual')
    try:
        ctx = ar._build_agent_context(project, incognito=False, task='continue the work',
                                       session_id=sid)
    finally:
        from mc.state import agent_sessions
        agent_sessions.pop(sid, None)
    assert 'MEMORY MINT NEEDS ONE WORD' not in ctx


# ── 5. RESOLVE route ─────────────────────────────────────────────────────────

@pytest.fixture()
def resolve_env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import memory as mem
    from mc.blueprints import guide_routes as gr
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    project = {'id': 'p1', 'project_path': str(tmp_path)}
    monkeypatch.setattr(gr, 'load_project', lambda pid: project if pid == 'p1' else None)

    from mc.memory import write_topic_note
    fn = write_topic_note(
        project, 'mint_hivemind_close_deadbeef02', 'Hivemind closed: old thing',
        '**Trigger:** hivemind_close\n\nold thing', note_type='project',
        supersedes='unresolved', mint_candidates='candidate_note')
    assert fn

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    yield c, tmp_path, fn


def test_resolve_route_supersedes_rewrites_sentinel(resolve_env):
    c, tmp_path, fn = resolve_env
    resp = c.post(f'/api/project/p1/memory/mints/{fn}/resolve',
                   json={'verdict': 'supersedes', 'candidate': 'candidate_note'})
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True
    text = (tmp_path / fn).read_text(encoding='utf-8')
    assert 'supersedes: candidate_note' in text
    assert 'mint_candidates:' not in text


def test_resolve_route_rejects_path_traversal(resolve_env):
    c, tmp_path, fn = resolve_env
    resp = c.post('/api/project/p1/memory/mints/../../etc/passwd/resolve',
                   json={'verdict': 'supersedes', 'candidate': 'x'})
    assert resp.status_code in (400, 404)


def test_resolve_route_404_when_not_pending(resolve_env):
    c, tmp_path, fn = resolve_env
    c.post(f'/api/project/p1/memory/mints/{fn}/resolve',
           json={'verdict': 'unrelated_to', 'candidate': 'candidate_note'})
    resp = c.post(f'/api/project/p1/memory/mints/{fn}/resolve',
                   json={'verdict': 'supersedes', 'candidate': 'candidate_note'})
    assert resp.status_code == 404
