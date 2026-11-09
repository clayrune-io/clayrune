"""Independent r9 review probes. Temporary fixtures and fake provider processes only."""
import sys
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'mc' / 'blueprints').is_dir())
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import conftest  # Apply the project's temporary-data/environment guards before server imports.
from conftest import _no_live_state_writes
from mc.blueprints import agent_routes as mod
from test_mc1165_round9 import ar, _claude_setup, CSID
from test_scheduler_routes import ctx, _seed_schedules
from test_mc1165_review8 import _run_now_setup

REAL_DUP = mod._refuse_duplicate_spawn


@pytest.fixture(autouse=True)
def _real_duplicate_guard(ar, monkeypatch):
    """The shared ar fixture stubs this guard; every probe exercises the real one."""
    monkeypatch.setattr(ar, '_refuse_duplicate_spawn', REAL_DUP)


def _held(p, sid, *, live=False, provider='claude'):
    return dict(session_id=sid, project_id=p['id'], status='completed',
                proc=SimpleNamespace(poll=lambda: None) if live else None,
                provider=provider, claude_session_id=CSID if provider == 'claude' else '',
                log_lines=[], _worktree_isolated=True)


def test_real_duplicate_guard_still_yields_isolated_refusal(ar, tmp_path, monkeypatch):
    p, spawned = _claude_setup(ar, tmp_path, monkeypatch)
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('', 0))
    monkeypatch.setattr(ar, '_fragile_resume_target', lambda *a, **k: False)
    sid = 'realguard001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / sid
    tree.mkdir(parents=True)
    monkeypatch.setitem(ar.agent_sessions, sid, _held(p, sid, live=True))
    with pytest.raises(ar._isolated_launch.RevivalRefused):
        ar._dispatch_agent_internal(p['id'], 'resume', resume_id=CSID,
            reuse_session_id=sid, agent_cwd_override=str(tree), isolated_override=True)
    assert not spawned


def test_run_now_real_dispatch_live_owner_returns_409_and_records_error(ar, ctx, tmp_path, monkeypatch):
    p, spawned = _claude_setup(ar, tmp_path, monkeypatch)
    p['id'] = 'testproj'
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('', 0))
    monkeypatch.setattr(ar, '_fragile_resume_target', lambda *a, **k: False)
    sid, newest = 'realrun00001', 'newempty0001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / sid
    tree.mkdir(parents=True)
    held = _held(p, sid, live=True)
    monkeypatch.setitem(ar.agent_sessions, sid, held)
    _run_now_setup(ctx, p, monkeypatch, sid, flagged=True)
    rows = [dict(session_id=newest, trigger_type='schedule', trigger_id='s1',
                 provider='claude', status='error'),
            dict(session_id=sid, trigger_type='schedule', trigger_id='s1',
                 provider='claude', claude_session_id=CSID, worktree_isolated=True)]
    monkeypatch.setattr(ctx.sr, '_load_agent_log', lambda _: rows)
    monkeypatch.setattr(ar, '_load_agent_log', lambda _: rows)
    monkeypatch.setattr(ctx.sr, '_latest_session_id_for_schedule', lambda *a: newest)
    # Use the real continuation path: the newest row has no native id to revive.
    monkeypatch.setattr(ctx.sr, '_scheduled_continue', REAL_CONTINUE)
    monkeypatch.setattr(ctx.sr, '_revive_from_agent_log', ar._revive_from_agent_log)
    monkeypatch.setattr(ctx.sr, '_revive_non_claude_from_agent_log', ar._revive_non_claude_from_agent_log)
    monkeypatch.setattr(ctx.sr, '_latest_claude_sid_for_schedule', lambda *a: CSID)
    monkeypatch.setattr(ctx.sr, '_isolated_resume_kwargs', lambda *a, **k:
        dict(agent_cwd_override=str(tree), isolated_override=True))
    monkeypatch.setattr(ctx.sr, '_dispatch_agent_internal', ar._dispatch_agent_internal)
    resp = ctx.client.post('/api/schedule/s1/run-now')
    print('HTTP', resp.status_code, resp.get_json(), 'schedule', json.loads(ctx.sched_path.read_text()))
    assert not spawned and ar.agent_sessions[sid] is held
    assert resp.status_code == 409
    assert resp.get_json()['refused'] is True
    assert resp.get_json()['session_id'] == sid
    saved = json.loads(ctx.sched_path.read_text())[0]
    assert saved['last_error'] == resp.get_json()['error']
    assert saved['last_error_at']


from mc.blueprints.scheduler_routes import _scheduled_continue as REAL_CONTINUE


def test_claude_rechecks_liveness_after_context_build(ar, tmp_path, monkeypatch):
    p, spawned = _claude_setup(ar, tmp_path, monkeypatch)
    sid = 'locked000001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / sid
    tree.mkdir(parents=True)
    held = _held(p, sid)
    monkeypatch.setitem(ar.agent_sessions, sid, held)
    def context(*a, **k):
        held['status'] = 'running'
        return 'CTX'
    monkeypatch.setattr(ar, '_build_agent_context', context)
    with pytest.raises(ar._isolated_launch.RevivalRefused):
        ar._dispatch_agent_internal(p['id'], 'resume', resume_id=CSID,
            reuse_session_id=sid, agent_cwd_override=str(tree), isolated_override=True)
    assert not spawned and ar.agent_sessions[sid] is held


@pytest.mark.parametrize('during_context', [False, True])
def test_different_live_owner_still_refused(ar, tmp_path, monkeypatch, during_context):
    p, spawned = _claude_setup(ar, tmp_path, monkeypatch)
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('', 0))
    monkeypatch.setattr(ar, '_fragile_resume_target', lambda *a, **k: False)
    sid, other = 'reuse0000001', 'other0000001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / sid
    tree.mkdir(parents=True)
    held = _held(p, sid)
    live = _held(p, other, live=True)
    monkeypatch.setitem(ar.agent_sessions, sid, held)
    if during_context:
        def context(*a, **k):
            monkeypatch.setitem(ar.agent_sessions, other, live)
            return 'CTX'
        monkeypatch.setattr(ar, '_build_agent_context', context)
    else:
        monkeypatch.setitem(ar.agent_sessions, other, live)
    with pytest.raises(ValueError, match='already running'):
        ar._dispatch_agent_internal(p['id'], 'resume', resume_id=CSID,
            reuse_session_id=sid, agent_cwd_override=str(tree), isolated_override=True)
    assert not spawned
    assert ar.agent_sessions[sid] is held
    assert ar.agent_sessions[other] is live


@pytest.mark.parametrize('isolated', [True, False])
def test_vendor_real_runtime_adapter_retained_id(ar, tmp_path, monkeypatch, isolated):
    p, _ = _claude_setup(ar, tmp_path, monkeypatch)
    sid = 'vendkeep0001'
    tree = Path(p['project_path']) / '.clayrune' / 'agents' / sid
    tree.mkdir(parents=True)
    held = _held(p, sid, provider='codex')
    monkeypatch.setitem(ar.agent_sessions, sid, held)
    from mc.agent_runtime import SessionHandle
    runtime = ar._agent_runtime.get_runtime('codex')
    launched = []
    def dispatch(**kw):
        launched.append(kw)
        return SessionHandle(mc_session_id=kw['mc_session_id'], provider='codex',
            mode='A', project_path=kw['project_path'], project_id=p['id'],
            session_dict=kw['session_dict'], started_at='')
    monkeypatch.setattr(runtime, 'dispatch', dispatch)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_runtime_lifecycle_service', None)
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_bridge_images_for_blind_model', lambda task, *a, **k: task)
    result = ar._dispatch_agent_internal(p['id'], 'resume', resume_id='codex-native',
        reuse_session_id=sid, provider_override='codex',
        agent_cwd_override=str(tree) if isolated else None, isolated_override=isolated)
    assert len(launched) == 1
    assert (result == sid) is isolated
    assert launched[0]['mc_session_id'] == result
    assert ar.agent_sessions[result] is launched[0]['session_dict']
    if isolated:
        assert ar.agent_sessions[result] is not held
        assert launched[0]['project_path'] == str(tree)
