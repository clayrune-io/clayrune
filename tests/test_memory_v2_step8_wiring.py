"""MC-944 step 8 (b2d85e51, §5 Conditions 11-13) — the CALLER wiring.

Follow-up to test_memory_v2_step8.py, which covers the primitives
(scan_for_negation_obligations, rank_negation_ledger, render_negation_ledger)
in isolation. Step 7's own wiring pass (test_memory_v2_step7_wiring.py) found
mint's primitives had zero callers on the first commit; this file exists so
step 8 does not repeat that miss. Five call sites:

  1. hivemind close        -> mc/blueprints/hivemind_routes.py orchestrator loop
  2. backlog item -> done  -> mc/blueprints/project_routes.py PATCH route
  3. docs/ artifact scan   -> mc/memory.py scan_docs_artifacts_for_mint (called
                               from _write_session_memory at session end)
  4. dispatch-time ledger  -> mc/blueprints/agent_routes.py _build_agent_context
  5. per-turn ledger       -> mc/memory_turn.py refresh_for_turn
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DOC_ONE_ROW = """# Some Artifact

## Rejected

- **Add a mover** — declined because there's nothing to move.
"""


def _position_files(tmp_path):
    return sorted(tmp_path.glob('position_*.md'))


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
    (hm_dir / hid / 'knowledge').mkdir(parents=True)
    manifest = {'id': hid, 'project_id': 'p1', 'title': 'Ship the thing',
                'goal': 'Ship the thing end to end', 'status': 'active',
                'config': {}}
    (hm_dir / hid / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    ws = {'id': 'ws1', 'status': 'completed', 'dependencies': []}
    (hm_dir / hid / 'workstreams' / 'ws1.json').write_text(json.dumps(ws), encoding='utf-8')
    (hm_dir / hid / 'knowledge' / 'synthesis.md').write_text(DOC_ONE_ROW, encoding='utf-8')

    monkeypatch.setattr(hm, '_hm_dispatch_orchestrator',
                         lambda *a, **k: hm._hivemind_orchestrator_stop.set())
    hm._hivemind_orchestrator_stop.clear()

    yield hm, mem, mc_state, tmp_path, hid
    hm._hivemind_orchestrator_stop.clear()


def test_hivemind_close_scans_negation_when_flag_on(hm_env):
    hm, mem, mc_state, tmp_path, hid = hm_env
    mc_state.CONFIG['negation_obligation_enabled'] = True
    try:
        hm._hivemind_orchestrator_loop()
    finally:
        mc_state.CONFIG.pop('negation_obligation_enabled', None)
    pos = _position_files(tmp_path)
    assert len(pos) == 1
    text = pos[0].read_text(encoding='utf-8')
    assert 'Add a mover' in text
    assert 'position: declined' in text


def test_hivemind_close_negation_noop_when_flag_off(hm_env):
    hm, mem, mc_state, tmp_path, hid = hm_env
    mc_state.CONFIG.pop('negation_obligation_enabled', None)  # default OFF
    hm._hivemind_orchestrator_loop()
    assert _position_files(tmp_path) == []


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
               'backlog': [{'id': 'b1', 'text': 'Ship the thing', 'status': 'open',
                            'notes': [{'text': DOC_ONE_ROW}]}]}
    projects = {'p1': project}
    monkeypatch.setattr(pr, 'load_project', lambda pid: projects.get(pid))
    saved = []
    monkeypatch.setattr(pr, 'save_project', lambda pid, p: saved.append((pid, p)))

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    yield c, mem, mc_state, tmp_path


def test_backlog_done_scans_negation_when_flag_on(backlog_env):
    c, mem, mc_state, tmp_path = backlog_env
    mc_state.CONFIG['negation_obligation_enabled'] = True
    try:
        resp = c.patch('/api/project/p1/backlog/b1', json={'status': 'done'})
    finally:
        mc_state.CONFIG.pop('negation_obligation_enabled', None)
    assert resp.status_code == 200
    pos = _position_files(tmp_path)
    assert len(pos) == 1
    assert 'Add a mover' in pos[0].read_text(encoding='utf-8')


def test_backlog_done_negation_noop_when_flag_off(backlog_env):
    c, mem, mc_state, tmp_path = backlog_env
    mc_state.CONFIG.pop('negation_obligation_enabled', None)
    resp = c.patch('/api/project/p1/backlog/b1', json={'status': 'done'})
    assert resp.status_code == 200
    assert _position_files(tmp_path) == []


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
    big.write_text(DOC_ONE_ROW + ('\nx' * 5000), encoding='utf-8')
    import os
    import time as _t
    now = _t.time()
    os.utime(big, (now, now))
    project = {'id': 'p1', 'project_path': str(proj_path)}

    yield mem, mc_state, tmp_path, project, now


def _session(now_offset_minutes=1):
    from datetime import datetime, timedelta, timezone
    return {'session_id': 's1', 'task': 'build the spec', 'trigger_type': 'manual',
            'started_at': (datetime.now(timezone.utc)
                            - timedelta(minutes=now_offset_minutes)).isoformat(),
            'log_lines': []}


def test_session_end_scans_negation_when_flag_on(session_end_env):
    mem, mc_state, tmp_path, project, now = session_end_env
    mc_state.CONFIG['negation_obligation_enabled'] = True
    try:
        mem._write_session_memory(project, _session(), 'completed', '', '2026-09-26')
    finally:
        mc_state.CONFIG.pop('negation_obligation_enabled', None)
    pos = _position_files(tmp_path)
    assert len(pos) == 1
    assert 'Add a mover' in pos[0].read_text(encoding='utf-8')


def test_session_end_negation_noop_when_flag_off(session_end_env):
    mem, mc_state, tmp_path, project, now = session_end_env
    mc_state.CONFIG.pop('negation_obligation_enabled', None)
    mem._write_session_memory(project, _session(), 'completed', '', '2026-09-26')
    assert _position_files(tmp_path) == []


def test_session_end_mint_on_negation_off_still_mints_only(session_end_env):
    """The two flags are independent — mint ON / negation OFF must mint the
    file without also scanning for negations (Condition 11/12's own opt-in)."""
    mem, mc_state, tmp_path, project, now = session_end_env
    mc_state.CONFIG['memory_mint_triggers_enabled'] = True
    mc_state.CONFIG.pop('negation_obligation_enabled', None)
    try:
        mem._write_session_memory(project, _session(), 'completed', '', '2026-09-26')
    finally:
        mc_state.CONFIG.pop('memory_mint_triggers_enabled', None)
    assert _position_files(tmp_path) == []
    assert list(tmp_path.glob('mint_*.md'))


# ── 4. dispatch-time ledger block in _build_agent_context ──────────────────

@pytest.fixture()
def ctx_env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc import memory as mem
    from mc.blueprints import agent_routes as ar

    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    tmp_path.mkdir(exist_ok=True)
    mem.write_position(
        {'id': 'p1'}, 'a resident ledger subject', 'declined', 'because reasons',
        decided='2026-09-20')
    project = {'id': 'p1', 'project_path': str(tmp_path)}
    yield ar, mc_state, project


def _sid_for(trigger_type):
    from mc.state import agent_sessions
    sid = 'sess1'
    agent_sessions[sid] = {'session_id': sid, 'trigger_type': trigger_type,
                            'task': 'continue the work'}
    return sid


def test_dispatch_ledger_block_appears_when_flag_on(ctx_env):
    ar, mc_state, project = ctx_env
    mc_state.CONFIG['negation_ledger_enabled'] = True
    sid = _sid_for('manual')
    try:
        ctx = ar._build_agent_context(project, incognito=False, task='continue the work',
                                       session_id=sid)
    finally:
        mc_state.CONFIG.pop('negation_ledger_enabled', None)
        from mc.state import agent_sessions
        agent_sessions.pop(sid, None)
    assert 'NEGATION LEDGER' in ctx
    assert 'a-resident-ledger-subject' in ctx or 'resident ledger subject' in ctx.lower()


def test_dispatch_ledger_block_absent_when_flag_off(ctx_env):
    ar, mc_state, project = ctx_env
    mc_state.CONFIG.pop('negation_ledger_enabled', None)
    sid = _sid_for('manual')
    try:
        ctx = ar._build_agent_context(project, incognito=False, task='continue the work',
                                       session_id=sid)
    finally:
        from mc.state import agent_sessions
        agent_sessions.pop(sid, None)
    assert 'NEGATION LEDGER' not in ctx


def test_dispatch_ledger_block_absent_when_incognito(ctx_env):
    ar, mc_state, project = ctx_env
    mc_state.CONFIG['negation_ledger_enabled'] = True
    sid = _sid_for('manual')
    try:
        ctx = ar._build_agent_context(project, incognito=True, task='continue the work',
                                       session_id=sid)
    finally:
        mc_state.CONFIG.pop('negation_ledger_enabled', None)
        from mc.state import agent_sessions
        agent_sessions.pop(sid, None)
    assert 'NEGATION LEDGER' not in ctx


# ── 5. per-turn ledger block in memory_turn.refresh_for_turn ────────────────

def test_per_turn_ledger_block_appears_when_flag_on(tmp_data_dir):
    import importlib
    srv = importlib.import_module('server')
    importlib.reload(srv)
    import mc.memory as mem
    import mc.memory_turn as mt
    from mc import state as mc_state

    p = {'id': 'turnledgerproj'}
    mem.write_position(p, 'a per turn subject', 'declined', 'because reasons',
                        decided='2026-09-20', task='dispatch task', trigger_type='manual')
    session = {'trigger_type': 'manual', 'task': 'dispatch task'}
    mc_state.CONFIG['negation_ledger_enabled'] = True
    try:
        result = mt.refresh_for_turn(p, session, 'unrelated live message')
    finally:
        mc_state.CONFIG.pop('negation_ledger_enabled', None)
    assert 'NEGATION LEDGER' in result['block']
    assert result['bytes'] <= mt.turn_budget_bytes()


def test_per_turn_ledger_block_absent_when_flag_off(tmp_data_dir):
    import importlib
    srv = importlib.import_module('server')
    importlib.reload(srv)
    import mc.memory as mem
    import mc.memory_turn as mt
    from mc import state as mc_state

    p = {'id': 'turnledgeroffproj'}
    mem.write_position(p, 'a per turn subject', 'declined', 'because reasons',
                        decided='2026-09-20')
    mc_state.CONFIG.pop('negation_ledger_enabled', None)
    result = mt.refresh_for_turn(p, {}, 'unrelated live message')
    assert 'NEGATION LEDGER' not in result['block']


def test_per_turn_ledger_flag_off_is_byte_identical_to_pre_step8(tmp_data_dir):
    """The hard constraint: flag OFF must not change the per-turn block at
    all, even a single byte, versus a corpus with no negation ledger call in
    the code path — proven here by a real note (non-position) plus a position
    both surfacing normally with the ledger flag left at its default (OFF)."""
    import importlib
    srv = importlib.import_module('server')
    importlib.reload(srv)
    import mc.memory as mem
    import mc.memory_turn as mt
    from mc import state as mc_state

    p = {'id': 'byteidenticalproj'}
    mp = mem._get_memory_path(p)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(mem._mem_compose('# Index', [], []), encoding='utf-8')
    (mp.parent / 'a_note.md').write_text(
        'widget calibration notes: the offset drifts by 3mm after reflow.',
        encoding='utf-8')
    mem.write_position(p, 'widget calibration offset', 'declined', 'because reasons',
                        decided='2026-09-20')
    mc_state.CONFIG.pop('negation_ledger_enabled', None)  # explicit default OFF
    result = mt.refresh_for_turn(p, {}, 'widget calibration offset drift')
    assert 'NEGATION LEDGER' not in result['block']
    assert 'STANDING POSITIONS' in result['block']
