"""MC-1165 r7: the two resume paths that dispatch themselves, bypassing the r6
revival functions, now take the same isolation boundary (`_isolated_resume_kwargs`).

1. `_revive_parent_for_delegation`, non-Claude branch (Fenn review 6, P1).
2. the scheduler's cold resume, reached by a `steward_respawn` cycle (P2).

Temp git repos; `_dispatch_agent_internal` is a recorder; no provider launches."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mc import agent_worktree as w, isolated_launch as launch, state  # noqa: E402
from test_cold_review_wiring import project, routes  # noqa: E402,F401
from test_merge_review_gate import repo  # noqa: E402,F401
from test_mc1165_review4_probes import _agent  # noqa: E402
from test_mc1165_review5_probes import _vendor_entry  # noqa: E402
from test_scheduler_routes import ctx, _due_cron_row, _seed_schedules, _OneShotStop  # noqa: E402,F401


# ── 1. delegation revival of a non-Claude parent ─────────────────────────────

def _delegation_setup(routes, project, monkeypatch, sid, entry, tree_for_resume=''):
    """Records every dispatch's raw kwargs. The fake mirrors the real dispatch's
    cwd order (override, else `_resume_cwd_for` -> tree marked NOT isolated, else
    the lone-agent decision = project checkout) so the tree-present case reports
    the flag the real dispatch would set, not just the directory."""
    entry = {**entry, 'status': 'completed', 'agent_model': 'gpt-6'}
    monkeypatch.setattr(routes, '_load_agent_log', lambda *a: [entry])
    monkeypatch.setattr(routes, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setattr(routes, '_model_quota_blocked', lambda *a, **k: '')
    monkeypatch.setitem(state.CONFIG, 'worktree_isolation_enabled', True)
    monkeypatch.setattr(routes, '_resume_cwd_for', lambda *a, **k: tree_for_resume or None)
    seen = []

    def fake_dispatch(pid, message, **kw):
        if kw.get('agent_cwd_override'):
            cwd, iso = kw['agent_cwd_override'], bool(kw.get('isolated_override'))
        elif tree_for_resume:
            cwd, iso = tree_for_resume, False
        else:
            cwd, iso = routes._maybe_isolate_worktree(project, kw.get('reuse_session_id') or 'x')
        seen.append({'cwd': cwd, 'isolated': iso, 'kw': kw})
        return sid
    monkeypatch.setattr(routes, '_dispatch_agent_internal', fake_dispatch)
    return seen


@pytest.mark.parametrize('tree_present', [True, False])
def test_delegation_vendor_parent_flagged_row_keeps_isolation(routes, project, monkeypatch, tree_present):
    sid = 'codexpar0001'
    tree, _ = _agent(project, sid)
    if not tree_present:
        assert w.remove(project, sid, delete_branch=True, force=True)[0]
    seen = _delegation_setup(routes, project, monkeypatch, sid, _vendor_entry(sid),
                             tree_for_resume=str(tree) if tree_present else '')
    out = routes._revive_parent_for_delegation('testproj', sid, 'child done', project)
    assert out['revived'] is True
    assert len(seen) == 1
    assert seen[0]['kw']['isolated_override'] is True
    assert seen[0]['kw']['agent_cwd_override'] == str(w.worktree_path(project, sid))
    assert seen[0]['isolated'] is True and seen[0]['cwd'] != project['project_path']
    assert Path(seen[0]['cwd']).is_dir()
    # the resume itself is unchanged: same native thread, same MC session
    assert seen[0]['kw']['resume_id'] == 'codex-thread-1'
    assert seen[0]['kw']['reuse_session_id'] == sid


def test_delegation_vendor_parent_is_blocked_when_its_tree_cannot_be_had(routes, project, monkeypatch):
    sid = 'codexpar0002'
    seen = _delegation_setup(routes, project, monkeypatch, sid, _vendor_entry(sid))
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    with pytest.raises(routes.DeliveryBlocked, match='no disk'):
        routes._revive_parent_for_delegation('testproj', sid, 'child done', project)
    assert seen == []          # refused before any process: nothing dispatched in main


def test_delegation_vendor_parent_without_an_isolation_record_is_unchanged(routes, project, monkeypatch):
    """Control: no flag, no tree, no branch -> no override; dispatch decides as before."""
    sid = 'codexpar0003'
    seen = _delegation_setup(routes, project, monkeypatch, sid, _vendor_entry(sid, flagged=False))
    routes._revive_parent_for_delegation('testproj', sid, 'child done', project)
    assert len(seen) == 1
    assert 'agent_cwd_override' not in seen[0]['kw'] and 'isolated_override' not in seen[0]['kw']
    assert seen[0]['isolated'] is False


# ── 2. steward_respawn: cold resume from the scheduler loop ──────────────────

def _steward_setup(ctx, project, monkeypatch, sid, *, flagged, respawn=True):
    sr = ctx.sr
    row = {'session_id': sid, 'trigger_type': 'schedule', 'trigger_id': 's1',
           'provider': 'claude', 'claude_session_id': 'csid-1', 'ts': '2026-11-08T00:00:00Z'}
    if flagged:
        row['worktree_isolated'] = True
    activity = []
    monkeypatch.setattr(sr, 'load_project', lambda pid: project)
    monkeypatch.setattr(sr, '_load_agent_log', lambda pid: [row])
    monkeypatch.setattr(sr, '_log_agent_activity', lambda pid, msg: activity.append(msg))
    monkeypatch.setattr(sr, '_steward_cycle_task', lambda pid: ('refreshed task', respawn))
    monkeypatch.setattr(sr, '_steward_cycle_running', lambda pid: False)
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda pid, s: 'csid-1')
    monkeypatch.setattr(sr, '_schedule_resume_provider', lambda *a: 'claude')
    monkeypatch.setattr(sr, '_newest_run_session_id_for_schedule', lambda pid, s: sid)
    monkeypatch.setitem(state.CONFIG, 'worktree_isolation_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'scheduler_paused', False)
    monkeypatch.setattr(sr, '_scheduler_stop', _OneShotStop())
    _seed_schedules(ctx, [_due_cron_row(project_id='testproj', steward=True)])
    return activity


def _loop(ctx):
    ctx.sr._scheduler_loop()
    return json.loads(ctx.sched_path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('tree_present', [True, False])
def test_steward_respawn_of_an_isolated_session_resumes_in_its_worktree(
        ctx, routes, project, monkeypatch, tree_present):
    sid = 'stewiso00001'
    _agent(project, sid)
    if not tree_present:
        assert w.remove(project, sid, delete_branch=True, force=True)[0]
    _steward_setup(ctx, project, monkeypatch, sid, flagged=True)
    rows = _loop(ctx)
    assert len(ctx.dispatch.calls) == 1
    call = ctx.dispatch.calls[0]
    assert call['resume_id'] == 'csid-1' and call['reuse_session_id'] == sid
    assert call['isolated_override'] is True
    assert call['agent_cwd_override'] == str(w.worktree_path(project, sid)) != project['project_path']
    assert Path(call['agent_cwd_override']).is_dir()
    assert 'last_error' not in rows[0]


def test_steward_respawn_is_a_failed_run_when_the_tree_cannot_be_had(ctx, routes, project, monkeypatch):
    sid = 'stewiso00002'
    activity = _steward_setup(ctx, project, monkeypatch, sid, flagged=True)
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    rows = _loop(ctx)
    assert ctx.dispatch.calls == []                     # never dispatched in main
    assert 'no disk' in rows[0]['last_error'] and rows[0]['last_error_at']
    assert 'last_run' not in rows[0]
    assert any('Scheduled run FAILED' in a and 'no disk' in a for a in activity)
    assert rows[0]['next_run'] != '2020-01-01T00:00:00Z'  # cadence still advances


def test_steward_respawn_of_a_non_isolated_session_is_unchanged(ctx, routes, project, monkeypatch):
    sid = 'stewplain001'
    _steward_setup(ctx, project, monkeypatch, sid, flagged=False)
    rows = _loop(ctx)
    assert len(ctx.dispatch.calls) == 1
    call = ctx.dispatch.calls[0]
    assert call['resume_id'] == 'csid-1' and call['reuse_session_id'] == sid
    assert 'agent_cwd_override' not in call and 'isolated_override' not in call
    assert 'last_error' not in rows[0]
