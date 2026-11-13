"""MC-1165 r8: Run Now's cold-resume fall-through takes the isolation boundary.

`schedule_run_now` calls `_scheduled_continue`; when that returns None it falls to
`_dispatch_agent_internal(reuse_session_id=...)`. Before r8 that call carried no
isolation kwargs, so Run Now on a worktree-isolated session resumed in the main
checkout. It now applies `_isolated_resume_kwargs` like the r7 scheduler branch.

Temp git repos; `_dispatch_agent_internal` is a recorder; no provider launches."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mc import agent_worktree as w, state  # noqa: E402
from test_cold_review_wiring import project, routes  # noqa: E402,F401
from test_merge_review_gate import repo  # noqa: E402,F401
from test_mc1165_review4_probes import _agent  # noqa: E402
from test_scheduler_routes import ctx, _seed_schedules  # noqa: E402,F401


def _run_now_setup(ctx, project, monkeypatch, sid, *, flagged):
    """Run Now on a continuing schedule whose `_scheduled_continue` returns None
    (the fall-through), with a prior MC row `sid` to cold-resume."""
    sr = ctx.sr
    row = {'session_id': sid, 'trigger_type': 'schedule', 'trigger_id': 's1',
           'provider': 'claude', 'claude_session_id': 'csid-1', 'ts': '2026-11-08T00:00:00Z'}
    if flagged:
        row['worktree_isolated'] = True
    monkeypatch.setattr(sr, 'load_project', lambda pid: project)
    monkeypatch.setattr(sr, '_load_agent_log', lambda pid, **_kw: [row])
    monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda pid, s: sid)
    monkeypatch.setattr(sr, '_scheduled_continue', lambda *a, **k: None)
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda pid, s: 'csid-1')
    monkeypatch.setattr(sr, '_schedule_resume_provider', lambda *a: 'claude')
    monkeypatch.setattr(sr, '_newest_run_session_id_for_schedule', lambda pid, s: sid)
    monkeypatch.setitem(state.CONFIG, 'worktree_isolation_enabled', True)
    _seed_schedules(ctx, [{'id': 's1', 'project_id': 'testproj', 'task': 'continue report',
                           'continue_session': True, 'enabled': True}])


def _rows(ctx):
    return json.loads(ctx.sched_path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('tree_present', [True, False])
def test_run_now_of_an_isolated_session_resumes_in_its_worktree(
        ctx, routes, project, monkeypatch, tree_present):
    sid = 'runnowiso001'
    _agent(project, sid)
    if not tree_present:
        assert w.remove(project, sid, delete_branch=True, force=True)[0]
    _run_now_setup(ctx, project, monkeypatch, sid, flagged=True)
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 200 and resp.get_json()['ok'] is True
    assert len(ctx.dispatch.calls) == 1
    call = ctx.dispatch.calls[0]
    assert call['resume_id'] == 'csid-1' and call['reuse_session_id'] == sid
    assert call['isolated_override'] is True
    assert call['agent_cwd_override'] == str(w.worktree_path(project, sid)) != project['project_path']
    assert Path(call['agent_cwd_override']).is_dir()
    assert 'last_error' not in _rows(ctx)[0]


def test_run_now_is_refused_when_the_isolated_tree_cannot_be_had(ctx, routes, project, monkeypatch):
    sid = 'runnowiso002'
    _run_now_setup(ctx, project, monkeypatch, sid, flagged=True)
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    resp = ctx.client.post('/api/schedule/s1/run-now')
    body = resp.get_json()
    assert resp.status_code == 409
    assert body['ok'] is False and body['refused'] is True and 'no disk' in body['error']
    assert body['session_id'] == sid
    assert ctx.dispatch.calls == []                     # never dispatched in main
    row = _rows(ctx)[0]
    assert 'no disk' in row['last_error'] and row['last_error_at']
    assert 'last_run' not in row


def test_run_now_of_a_non_isolated_session_is_unchanged(ctx, routes, project, monkeypatch):
    sid = 'runnowplain1'
    _run_now_setup(ctx, project, monkeypatch, sid, flagged=False)
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 200 and resp.get_json()['resumed'] is True
    assert len(ctx.dispatch.calls) == 1
    call = ctx.dispatch.calls[0]
    assert call['resume_id'] == 'csid-1' and call['reuse_session_id'] == sid
    assert 'agent_cwd_override' not in call and 'isolated_override' not in call
    assert 'last_error' not in _rows(ctx)[0]
