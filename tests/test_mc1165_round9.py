"""MC-1165 round 9. Fenn's review-7 probes (1f5039b3) plus the round's own tests.

P1 (Dave's call): an isolated dispatch with a reuse id keeps that id; a retained
dead dict is replaced, a live one is refused. P2 and the finish-time guard too."""
from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tests'))
from mc import agent_worktree as w, state  # noqa: E402
from tests.test_revive_notify_carry import ar, _project, CSID  # noqa: E402,F401
from test_cold_review_wiring import project, routes  # noqa: E402,F401
from test_merge_review_gate import repo  # noqa: E402,F401
from test_mc1165_review4_probes import _agent  # noqa: E402
from test_scheduler_routes import ctx, _due_cron_row, _seed_schedules, _OneShotStop  # noqa: E402,F401
from test_mc1165_review7 import _steward_setup, _loop  # noqa: E402


class _Stdin:
    def write(self, *_): pass
    def flush(self): pass


def _capture(ar_, monkeypatch):
    monkeypatch.setattr(ar_.subprocess, 'Popen', lambda cmd, *a, **k: SimpleNamespace(
        pid=555, stdin=_Stdin(), stdout=iter([]), poll=lambda: None))
    monkeypatch.setattr(ar_._turn_clock, 'gap_briefing', lambda *a, **k: '')


# P1 candidate: the override is applied to a FRESH session id when the reused
# id is still in agent_sessions (completed, not purged). The tree is S's, the
# session that runs in it and is flagged isolated is D.
def test_override_with_retained_reuse_sid_keeps_the_session_id(ar, tmp_path, monkeypatch):
    p = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: p)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: [])
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    _capture(ar, monkeypatch)
    S = 'stewret00001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / S
    tree.mkdir(parents=True)
    monkeypatch.setitem(ar.agent_sessions, S, {
        'session_id': S, 'project_id': 'p1', 'status': 'completed',
        'process_alive': False, 'proc': None, 'log_lines': []})
    sid = ar._dispatch_agent_internal('p1', 'cycle', resume_id=CSID, reuse_session_id=S,
                                      agent_cwd_override=str(tree), isolated_override=True)
    sess = ar.agent_sessions[sid]
    print('dispatched sid', sid, 'cwd', sess.get('_agent_cwd'), 'iso', sess.get('_worktree_isolated'))
    assert sid == S, (f'tree {S} handed to fresh session {sid} flagged isolated='
                      f'{sess.get("_worktree_isolated")}; its merge-back looks for '
                      f'{w.branch_name(sid)}, not {w.branch_name(S)}')


# Q3: the flag is read for reuse_sid (newest row) while the conversation resumed
# is resume_id (newest row WITH a native id). When they differ, the isolated
# owner's conversation resumes with no override.
def test_scheduler_flag_read_for_the_row_that_owns_resume_id(ctx, routes, project, monkeypatch):
    A, B = 'stewownA0001', 'stewnewB0001'
    _agent(project, A)
    _steward_setup(ctx, project, monkeypatch, A, flagged=True)
    sr = ctx.sr
    rows = [  # newest first
        {'session_id': B, 'trigger_type': 'schedule', 'trigger_id': 's1', 'provider': 'claude',
         'ts': '2026-11-08T01:00:00Z', 'status': 'error'},
        {'session_id': A, 'trigger_type': 'schedule', 'trigger_id': 's1', 'provider': 'claude',
         'claude_session_id': 'csid-1', 'worktree_isolated': True, 'ts': '2026-11-08T00:00:00Z'},
    ]
    monkeypatch.setattr(sr, '_load_agent_log', lambda pid, **_kw: rows)
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule',
                        lambda pid, s: sr._latest_resume_for_schedule(pid, s)[0])
    # real helper body (scheduler_routes.py:1114): first schedule row with a session_id
    monkeypatch.setattr(sr, '_newest_run_session_id_for_schedule', lambda pid, s: rows[0]['session_id'])
    _loop(ctx)
    call = ctx.dispatch.calls[0]
    print('resume', call['resume_id'], 'reuse', call['reuse_session_id'],
          'override', call.get('agent_cwd_override'))
    assert call['resume_id'] == 'csid-1'
    assert call.get('isolated_override') is True, 'A owns csid-1 and is isolated; resumed with no override'


def test_vendor_override_with_retained_reuse_sid_keeps_the_session_id(ar, tmp_path, monkeypatch):
    p = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: p)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: [])
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a, **k: '')
    seen = {}

    def rec(p_, task, **kw):
        seen.update(kw)
        return kw.get('planned_session_id')
    monkeypatch.setattr(ar, '_dispatch_via_runtime', rec)
    S = 'codexret0001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / S
    tree.mkdir(parents=True)
    monkeypatch.setitem(ar.agent_sessions, S, {
        'session_id': S, 'project_id': 'p1', 'status': 'completed', 'provider': 'codex',
        'process_alive': False, 'proc': None, 'log_lines': []})
    sid = ar._dispatch_agent_internal('p1', 'cycle', resume_id='codex-thread-1', reuse_session_id=S,
                                      provider_override='codex', agent_cwd_override=str(tree),
                                      isolated_override=True)
    print('vendor planned', seen.get('planned_session_id'), 'cwd', seen.get('agent_cwd'), 'iso', seen.get('isolated'))
    assert sid == S


# ── round 9 own tests ───────────────────────────────────────────────────────

def test_resume_is_keyed_on_the_row_that_owns_it_and_dispatches_under_that_row(
        ctx, routes, project, monkeypatch):
    """Fenn's probe, with the dispatch row checked too: A owns csid-1, so the
    tree override AND the adopted MC row are A's, not the csid-less newest B."""
    A, B = 'stewownA0002', 'stewnewB0002'
    _agent(project, A)
    _steward_setup(ctx, project, monkeypatch, A, flagged=True)
    sr = ctx.sr
    rows = [
        {'session_id': B, 'trigger_type': 'schedule', 'trigger_id': 's1', 'provider': 'claude',
         'ts': '2026-11-08T01:00:00Z', 'status': 'error'},
        {'session_id': A, 'trigger_type': 'schedule', 'trigger_id': 's1', 'provider': 'claude',
         'claude_session_id': 'csid-1', 'worktree_isolated': True, 'ts': '2026-11-08T00:00:00Z'},
    ]
    monkeypatch.setattr(sr, '_load_agent_log', lambda pid, **_kw: rows)
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule',
                        lambda pid, s: sr._latest_resume_for_schedule(pid, s)[0])
    monkeypatch.setattr(sr, '_newest_run_session_id_for_schedule', lambda pid, s: B)
    _loop(ctx)
    call = ctx.dispatch.calls[0]
    assert call['resume_id'] == 'csid-1'
    assert call['reuse_session_id'] == A
    assert call['isolated_override'] is True
    assert A in call['agent_cwd_override'] and B not in call['agent_cwd_override']


def test_run_now_keys_isolation_on_the_row_that_owns_the_resume(
        ctx, routes, project, monkeypatch):
    A, B = 'runownA00002', 'runnewB00002'
    _agent(project, A)
    from test_mc1165_review8 import _run_now_setup
    _run_now_setup(ctx, project, monkeypatch, A, flagged=True)
    sr = ctx.sr
    rows = [
        {'session_id': B, 'trigger_type': 'schedule', 'trigger_id': 's1', 'provider': 'claude',
         'ts': '2026-11-08T01:00:00Z', 'status': 'error'},
        {'session_id': A, 'trigger_type': 'schedule', 'trigger_id': 's1', 'provider': 'claude',
         'claude_session_id': 'csid-1', 'worktree_isolated': True, 'ts': '2026-11-08T00:00:00Z'},
    ]
    monkeypatch.setattr(sr, '_load_agent_log', lambda pid, **_kw: rows)
    monkeypatch.setattr(sr, '_newest_run_session_id_for_schedule', lambda pid, s: B)
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 200
    call = ctx.dispatch.calls[0]
    assert call['reuse_session_id'] == A and call['isolated_override'] is True
    assert A in call['agent_cwd_override']


def test_owner_lookup_falls_back_to_newest_row_when_no_row_owns_the_id(ctx, routes, monkeypatch):
    sr = ctx.sr
    monkeypatch.setattr(sr, '_load_agent_log', lambda pid, **_kw: [
        {'session_id': 'x1', 'trigger_type': 'schedule', 'trigger_id': 's1', 'provider': 'claude'}])
    assert sr._session_owning_resume('p', 's1', 'csid-9') == ''
    assert sr._session_owning_resume('p', 's1', '') == ''


# finish-time guard: a run holding a tree other than its own id's must not vanish

def _finish_session(project, sid, held):
    return {'session_id': sid, 'project_id': project['id'], '_worktree_isolated': True,
            '_agent_cwd': str(held), 'task': 't'}


def _run_finish(routes, project, monkeypatch, session):
    acts, logs = [], []
    monkeypatch.setattr(routes, 'load_project', lambda pid: project)
    monkeypatch.setattr(routes, '_log_agent_activity', lambda pid, msg: acts.append(msg))
    monkeypatch.setattr(routes, '_log', lambda *a, **k: logs.append((a, k)))
    routes._worktree_merge_back_on_end(session)
    return acts, logs


def test_finish_in_a_foreign_tree_logs_loudly_and_surfaces(routes, project, repo, monkeypatch):
    other = w.worktree_path(project, 'owner0000001')
    other.mkdir(parents=True)
    acts, logs = _run_finish(routes, project, monkeypatch, _finish_session(project, 'fresh0000001', other))
    assert any('NOT reviewed or merged' in a and 'clayrune/agent/fresh0000001' in a for a in acts)
    assert any(k.get('level') == 'error' and 'NOT reviewed or merged' in a[0] for a, k in logs)


def test_finish_in_its_own_tree_does_not_alarm(routes, project, repo, monkeypatch):
    own = w.worktree_path(project, 'own000000001')
    own.mkdir(parents=True)
    acts, _ = _run_finish(routes, project, monkeypatch, _finish_session(project, 'own000000001', own))
    assert not any('NOT reviewed or merged' in a for a in acts)
    own2 = w.worktree_path(project, 'own000000002')   # the first finish removed its tree
    sub = own2 / 'sub'
    sub.mkdir(parents=True)
    acts, _ = _run_finish(routes, project, monkeypatch, _finish_session(project, 'own000000002', sub))
    assert not any('NOT reviewed or merged' in a for a in acts)


def test_finish_guard_skips_hivemind_workers_and_non_isolated(routes, project, repo, monkeypatch):
    other = w.worktree_path(project, 'owner0000002')
    other.mkdir(parents=True)
    acts, _ = _run_finish(routes, project, monkeypatch, _finish_session(project, 'hm_abcd1234', other))
    assert not any('NOT reviewed or merged' in a for a in acts)
    plain = dict(_finish_session(project, 'plain0000001', other), _worktree_isolated=False)
    acts, _ = _run_finish(routes, project, monkeypatch, plain)
    assert not any('NOT reviewed or merged' in a for a in acts)


# ── P1: an isolated resume keeps its session id (Dave's call, round 9) ──────

def _claude_setup(ar, tmp_path, monkeypatch):
    p = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: p)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: [])
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    spawned = []
    monkeypatch.setattr(ar.subprocess, 'Popen', lambda cmd, *a, **k: spawned.append(cmd) or SimpleNamespace(
        pid=555, stdin=_Stdin(), stdout=iter([]), poll=lambda: None))
    monkeypatch.setattr(ar._turn_clock, 'gap_briefing', lambda *a, **k: '')
    return p, spawned


def test_dead_retained_dict_is_replaced_under_the_same_id(ar, tmp_path, monkeypatch):
    p, spawned = _claude_setup(ar, tmp_path, monkeypatch)
    S = 'stewret00002'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / S
    tree.mkdir(parents=True)
    dead = {'session_id': S, 'project_id': 'p1', 'status': 'completed',
            'process_alive': False, 'proc': None, 'log_lines': []}
    monkeypatch.setitem(ar.agent_sessions, S, dead)
    sid = ar._dispatch_agent_internal('p1', 'cycle', resume_id=CSID, reuse_session_id=S,
                                      agent_cwd_override=str(tree), isolated_override=True)
    assert sid == S and ar.agent_sessions[S] is not dead
    assert ar.agent_sessions[S]['_worktree_isolated'] is True and len(spawned) == 1


@pytest.mark.parametrize('live', [
    {'status': 'idle', 'proc': None},                       # idle Mode B between turns
    {'status': 'completed', 'proc': 'alive'},               # process still up
])
def test_live_retained_session_is_refused_not_forked_or_rerun(ar, tmp_path, monkeypatch, live):
    p, spawned = _claude_setup(ar, tmp_path, monkeypatch)
    acts = []
    monkeypatch.setattr(ar, '_log_agent_activity', lambda pid, m: acts.append(m))
    S = 'stewlive0001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / S
    tree.mkdir(parents=True)
    held = {'session_id': S, 'project_id': 'p1', 'status': live['status'], 'log_lines': [],
            'proc': SimpleNamespace(poll=lambda: None) if live['proc'] else None}
    monkeypatch.setitem(ar.agent_sessions, S, held)
    before = set(ar.agent_sessions)
    with pytest.raises(ar._isolated_launch.RevivalRefused) as ei:
        ar._dispatch_agent_internal('p1', 'cycle', resume_id=CSID, reuse_session_id=S,
                                    agent_cwd_override=str(tree), isolated_override=True)
    assert 'still running' in str(ei.value) and S[:12] in str(ei.value)
    assert spawned == [] and set(ar.agent_sessions) == before and ar.agent_sessions[S] is held
    assert any('Isolated resume refused' in a for a in acts)          # surfaced, not silent


def test_vendor_live_retained_session_is_refused(ar, tmp_path, monkeypatch):
    p = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: p)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **_kw: [])
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a, **k: '')
    ran = []
    monkeypatch.setattr(ar, '_dispatch_via_runtime', lambda *a, **k: ran.append(k))
    S = 'codexlive001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / S
    tree.mkdir(parents=True)
    monkeypatch.setitem(ar.agent_sessions, S, {
        'session_id': S, 'project_id': 'p1', 'status': 'running', 'provider': 'codex',
        'proc': None, 'log_lines': []})
    with pytest.raises(ar._isolated_launch.RevivalRefused):
        ar._dispatch_agent_internal('p1', 'cycle', resume_id='codex-thread-1', reuse_session_id=S,
                                    provider_override='codex', agent_cwd_override=str(tree),
                                    isolated_override=True)
    assert ran == []


def test_non_isolated_reuse_with_a_retained_session_still_gets_a_fresh_id(ar, tmp_path, monkeypatch):
    """Control: without `isolated_override` nothing about id selection changed."""
    p, spawned = _claude_setup(ar, tmp_path, monkeypatch)
    S = 'plainret0001'
    monkeypatch.setitem(ar.agent_sessions, S, {
        'session_id': S, 'project_id': 'p1', 'status': 'completed',
        'process_alive': False, 'proc': None, 'log_lines': []})
    sid = ar._dispatch_agent_internal('p1', 'cycle', resume_id=CSID, reuse_session_id=S)
    assert sid != S and 'p1' == ar.agent_sessions[sid]['project_id']


def test_scheduled_rerun_of_a_live_isolated_session_fails_the_run_and_spawns_nothing(
        ctx, routes, project, monkeypatch):
    """Alive S + a scheduled (steward_respawn) rerun: a failed run with the reason,
    never a second run in S's tree. The dispatch stand-in runs the REAL reuse guard."""
    from mc.blueprints import agent_routes as ar
    sid = 'stewlive0002'
    _agent(project, sid)
    activity = _steward_setup(ctx, project, monkeypatch, sid, flagged=True)
    monkeypatch.setitem(ar.agent_sessions, sid, {
        'session_id': sid, 'project_id': project['id'], 'status': 'idle', 'proc': None, 'log_lines': []})
    spawned = []

    def _dispatch(pid, task, **kw):
        if kw.get('isolated_override') and kw.get('reuse_session_id'):
            monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
            ar._guard_isolated_reuse(pid, kw['reuse_session_id'])
        spawned.append(kw)
        return 'x'
    monkeypatch.setattr(ctx.sr, '_dispatch_agent_internal', _dispatch)
    rows = _loop(ctx)
    assert spawned == []
    assert 'still running' in rows[0]['last_error'] and rows[0]['last_error_at']
    assert 'last_run' not in rows[0]
    assert any('Scheduled run FAILED' in a and 'still running' in a for a in activity)
    assert rows[0]['next_run'] != '2020-01-01T00:00:00Z'


def test_run_now_of_a_live_isolated_session_is_a_409_not_a_second_run(ctx, routes, project, monkeypatch):
    from mc.blueprints import agent_routes as ar
    from test_mc1165_review8 import _run_now_setup
    sid = 'runlive00002'
    _agent(project, sid)
    _run_now_setup(ctx, project, monkeypatch, sid, flagged=True)
    monkeypatch.setitem(ar.agent_sessions, sid, {
        'session_id': sid, 'project_id': project['id'], 'status': 'running', 'proc': None, 'log_lines': []})
    spawned = []

    def _dispatch(pid, task, **kw):
        monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
        ar._guard_isolated_reuse(pid, kw['reuse_session_id'])
        spawned.append(kw)
        return 'x'
    monkeypatch.setattr(ctx.sr, '_dispatch_agent_internal', _dispatch)
    resp = ctx.client.post('/api/schedule/s1/run-now')
    body = resp.get_json()
    assert resp.status_code == 409 and body['refused'] is True and 'still running' in body['error']
    assert spawned == []
