"""MC-1165 round 6: Fenn's review-5 repros, kept as regression tests (ported from
his probe file), plus the pins for the typed refusal. Temporary repos, process
creation faked, no provider launched. Pins: a refused isolated revival is a
`RevivalRefused` that every caller turns into a visible refusal (HTTP 409, a
failed scheduled run) and never into a fresh dispatch in the project checkout;
the non-Claude revival honours `worktree_isolated` the way the Claude one does;
the isolated Mode B revival does not hold the project lock across its stdin
write."""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import flask
import pytest
from mc import agent_worktree as w, isolated_launch as launch, state
from test_cold_review_wiring import project, routes  # noqa: F401
from test_merge_review_gate import repo, _git  # noqa: F401
from test_mc1165_review4_probes import revive_setup, _no_leaked_revived_sessions  # noqa: F401


def _call(view, pid, payload):
    app = flask.Flask('probe')
    with app.test_request_context(json=payload, method='POST'):
        rv = view(pid)
        resp = rv[0] if isinstance(rv, tuple) else rv
        code = rv[1] if isinstance(rv, tuple) else resp.status_code
        return code, resp.get_json()


# ── 1. a refusal is not "nothing to revive" ──────────────────────────────────

@pytest.mark.parametrize('route', ['send', 'followup'])
@pytest.mark.parametrize('streaming', [False, True])
def test_refused_isolated_revival_is_a_409_not_a_fresh_dispatch(routes, project, monkeypatch, streaming, route):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=True)
    _git(project['project_path'], 'branch', '-D', w.branch_name(sid))
    routes._load_agent_log('testproj')[0]['worktree_isolated'] = True
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    monkeypatch.setitem(state.CONFIG, 'worktree_isolation_enabled', True)
    fresh = []

    def fake_dispatch(pid, message, **kw):
        cwd, iso = routes._maybe_isolate_worktree(project, 'freshsid0001')  # real decision
        fresh.append({'cwd': cwd, 'isolated': iso, 'resume_id': kw.get('resume_id')})
        return 'freshsid0001'
    monkeypatch.setattr(routes, '_dispatch_agent_internal', fake_dispatch)
    view = routes.agent_send if route == 'send' else routes.agent_followup
    code, body = _call(view, 'testproj', {'session_id': sid, 'message': 'now fix the test you wrote'})
    assert not spawned
    assert not fresh, f'refused isolated revival re-run as a fresh dispatch: {fresh} -> {code} {body}'
    assert code == 409 and body['ok'] is False and body.get('refused') is True
    assert 'no disk' in body['error'] and 'not started in the project checkout' in body['error']


@pytest.mark.parametrize('streaming', [False, True])
def test_stale_tree_send_real_create_lone_agent(routes, project, monkeypatch, streaming):
    """Real create(): an isolated tree restored with a stale .git marker. No fakes on git."""
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=False)
    tree = Path(tree)
    marker = (tree / '.git').read_text()
    assert w.remove(project, sid, delete_branch=True, force=True)[0]
    tree.mkdir()
    (tree / '.git').write_text(marker)
    routes._load_agent_log('testproj')[0]['worktree_isolated'] = True
    monkeypatch.setitem(state.CONFIG, 'worktree_isolation_enabled', True)
    fresh = []

    def fake_dispatch(pid, message, **kw):
        cwd, iso = routes._maybe_isolate_worktree(project, 'freshsid0002')  # real decision, lone agent
        fresh.append({'cwd': cwd, 'isolated': iso})
        return 'freshsid0002'
    monkeypatch.setattr(routes, '_dispatch_agent_internal', fake_dispatch)
    code, body = _call(routes.agent_send, 'testproj', {'session_id': sid, 'message': 'now fix the test you wrote'})
    assert not spawned
    assert not fresh, f'request ran outside its worktree: {fresh} {body}'
    assert code == 409 and body.get('refused') is True


def _sched_wired(routes, monkeypatch):
    from mc.blueprints import scheduler_routes as sr
    monkeypatch.setattr(sr, '_revive_from_agent_log', routes._revive_from_agent_log)
    monkeypatch.setattr(sr, '_revive_non_claude_from_agent_log', routes._revive_non_claude_from_agent_log)
    monkeypatch.setattr(sr, 'get_manager', routes.get_manager)
    monkeypatch.setattr(sr, '_log_agent_activity', lambda *a, **k: None, raising=False)
    return sr


def test_scheduled_continue_raises_the_refusal_instead_of_returning_none(routes, project, monkeypatch):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, False, removed=True)
    _git(project['project_path'], 'branch', '-D', w.branch_name(sid))
    routes._load_agent_log('testproj')[0]['worktree_isolated'] = True
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    sr = _sched_wired(routes, monkeypatch)
    # None would send the cadence loop to _dispatch_agent_internal(resume_id=..,
    # reuse_session_id=<same sid>), which resumes the session in the project checkout.
    with pytest.raises(launch.RevivalRefused, match='no disk'):
        sr._scheduled_continue(project, 'testproj', sid, 'tick')
    assert not spawned


def test_scheduled_run_now_reports_the_refusal_and_does_not_dispatch(routes, project, monkeypatch):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, False, removed=True)
    _git(project['project_path'], 'branch', '-D', w.branch_name(sid))
    routes._load_agent_log('testproj')[0]['worktree_isolated'] = True
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    sr = _sched_wired(routes, monkeypatch)
    schedules = [{'id': 'sch1', 'project_id': 'testproj', 'task': 'tick', 'continue_session': True}]
    saved = []
    monkeypatch.setattr(sr, '_load_schedules', lambda: schedules)
    monkeypatch.setattr(sr._role_writes, 'save_runtime_schedules', lambda s: saved.append(list(s)))
    monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda *a: sid)
    monkeypatch.setattr(sr, 'load_project', lambda pid: project)
    dispatched = []
    monkeypatch.setattr(sr, '_dispatch_agent_internal', lambda *a, **k: dispatched.append(k) or 'x')
    view = sr.schedule_run_now
    app = flask.Flask('probe')
    with app.test_request_context(method='POST'):
        rv = view('sch1')
    resp, code = rv if isinstance(rv, tuple) else (rv, rv.status_code)
    body = resp.get_json()
    assert code == 409 and body['refused'] is True and 'no disk' in body['error']
    assert not dispatched and not spawned
    assert 'no disk' in schedules[0]['last_error']


def test_delegation_to_a_parent_that_cannot_be_restored_is_blocked_not_uncertain(routes, project, monkeypatch):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, False, removed=True)
    _git(project['project_path'], 'branch', '-D', w.branch_name(sid))
    entry = routes._load_agent_log('testproj')[0]
    entry.update(worktree_isolated=True, status='completed', agent_model='sonnet')
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    monkeypatch.setattr(routes, '_model_quota_blocked', lambda *a, **k: '')
    with pytest.raises(routes.DeliveryBlocked, match='no disk'):
        routes._revive_parent_for_delegation('testproj', sid, 'child done', project)
    assert not spawned


@pytest.mark.parametrize('streaming', [False, True])
def test_isolated_spawn_failure_is_a_refusal_not_none(routes, project, monkeypatch, streaming):
    """The tree is fine but the process will not start: None would fall through to a
    fresh dispatch just the same."""
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=False)

    def boom(*a, **k):
        raise OSError('cannot start')
    monkeypatch.setattr(routes, 'spawn_engine_proc', boom)
    with pytest.raises(launch.RevivalRefused, match='cannot start'):
        routes._revive_from_agent_log('testproj', sid, 'continue', project)


# ── 3. the stdin write must not hold the project lock ────────────────────────

def _blocking_spawn(spawned, in_write, release):
    def blocking_write(s):
        in_write.set()
        release.wait(10)          # a child that has not started reading stdin

    def spawn(*a, **kw):
        spawned.append(kw['cwd'])
        return SimpleNamespace(pid=12345, stdin=SimpleNamespace(write=blocking_write, flush=lambda: None))
    return spawn


@pytest.mark.parametrize('removed', [False, True])
def test_isolated_mode_b_revival_does_not_write_stdin_under_the_project_lock(routes, project, monkeypatch, removed):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, True, removed=removed)
    in_write, release = threading.Event(), threading.Event()
    monkeypatch.setattr(routes, 'spawn_engine_proc', _blocking_spawn(spawned, in_write, release))
    result = []
    t = threading.Thread(target=lambda: result.append(
        routes._revive_from_agent_log('testproj', sid, 'continue', project)), daemon=True)
    t.start()
    try:
        assert in_write.wait(10), f'never reached stdin write; spawned={spawned}'
        t.join(5)
        assert not t.is_alive(), 'revival blocked on the stdin write'
        lock = routes.get_manager('testproj').lock
        got = lock.acquire(timeout=2)
        if got:
            lock.release()
        assert got, 'project lock held across a blocking stdin write during isolated revival'
        assert result and result[0]['_worktree_isolated']
    finally:
        release.set()
        t.join(10)


def test_mode_b_revival_still_delivers_the_message_first(routes, project, monkeypatch):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, True, removed=False)
    written, done = [], threading.Event()

    def write(s):
        written.append(s)
        done.set()
    monkeypatch.setattr(routes, 'spawn_engine_proc', lambda *a, **kw: spawned.append(kw['cwd']) or SimpleNamespace(
        pid=12345, stdin=SimpleNamespace(write=write, flush=lambda: None)))
    session = routes._revive_from_agent_log('testproj', sid, 'continue the work', project)
    assert done.wait(5)
    assert 'continue the work' in written[0] and session['status'] == 'running'


# ── 2. the non-Claude revival honours worktree_isolated ──────────────────────

def _vendor_entry(sid, flagged=True):
    entry = {'session_id': sid, 'provider': 'codex', 'claude_session_id': '',
             'provider_session_id': 'codex-thread-1',
             'ts': '2026-11-08T00:00:00Z', 'task': 't'}
    if flagged:
        entry['worktree_isolated'] = True
    return entry


def _vendor_setup(routes, project, monkeypatch, sid, entry):
    monkeypatch.setattr(routes, '_load_agent_log', lambda *a: [entry])
    monkeypatch.setattr(routes, '_assert_runtime_project_generation', lambda *a, **k: None)
    monkeypatch.setitem(state.CONFIG, 'worktree_isolation_enabled', True)
    seen = []

    def fake_dispatch(pid, message, **kw):
        # What the real dispatch does: an override wins, otherwise the lone-agent
        # decision, which is the project checkout.
        if kw.get('agent_cwd_override'):
            seen.append({'cwd': kw['agent_cwd_override'], 'isolated': bool(kw.get('isolated_override'))})
        else:
            cwd, iso = routes._maybe_isolate_worktree(project, kw.get('reuse_session_id') or 'x')
            seen.append({'cwd': cwd, 'isolated': iso})
        return sid
    monkeypatch.setattr(routes, '_dispatch_agent_internal', fake_dispatch)
    return seen


@pytest.mark.parametrize('tree_present', [True, False])
def test_vendor_revival_of_flagged_row_runs_in_its_worktree(routes, project, monkeypatch, tree_present):
    from test_mc1165_review4_probes import _agent
    sid = 'codexrev0001'
    tree, _ = _agent(project, sid)
    if not tree_present:
        assert w.remove(project, sid, delete_branch=True, force=True)[0]
    seen = _vendor_setup(routes, project, monkeypatch, sid, _vendor_entry(sid))
    assert routes._revive_non_claude_from_agent_log('testproj', sid, 'continue my codex task', project) == sid
    assert len(seen) == 1 and seen[0]['isolated'] is True
    assert seen[0]['cwd'] == str(w.worktree_path(project, sid)) != project['project_path']
    assert Path(seen[0]['cwd']).is_dir()


def test_vendor_revival_of_flagged_row_is_refused_when_the_tree_cannot_be_had(routes, project, monkeypatch):
    sid = 'codexrev0002'
    seen = _vendor_setup(routes, project, monkeypatch, sid, _vendor_entry(sid))
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    with pytest.raises(launch.RevivalRefused, match='no disk'):
        routes._revive_non_claude_from_agent_log('testproj', sid, 'continue my codex task', project)
    assert seen == []


def test_vendor_revival_dispatch_failure_for_an_isolated_row_is_a_refusal(routes, project, monkeypatch):
    from test_mc1165_review4_probes import _agent
    sid = 'codexrev0003'
    _agent(project, sid)
    _vendor_setup(routes, project, monkeypatch, sid, _vendor_entry(sid))

    def boom(*a, **k):
        raise RuntimeError('provider down')
    monkeypatch.setattr(routes, '_dispatch_agent_internal', boom)
    with pytest.raises(launch.RevivalRefused, match='provider down'):
        routes._revive_non_claude_from_agent_log('testproj', sid, 'continue', project)


def test_vendor_revival_of_an_unflagged_row_with_no_tree_is_unchanged(routes, project, monkeypatch):
    """Control: no record of isolation anywhere -> no override, dispatch decides as before."""
    sid = 'codexrev0004'
    seen = _vendor_setup(routes, project, monkeypatch, sid, _vendor_entry(sid, flagged=False))
    assert routes._revive_non_claude_from_agent_log('testproj', sid, 'go', project) == sid
    assert seen[0]['isolated'] is False


def test_send_returns_409_for_a_refused_vendor_revival(routes, project, monkeypatch):
    sid = 'codexrev0005'
    seen = _vendor_setup(routes, project, monkeypatch, sid, _vendor_entry(sid))
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    code, body = _call(routes.agent_send, 'testproj', {'session_id': sid, 'message': 'go on'})
    assert code == 409 and body['refused'] is True and 'no disk' in body['error']
    assert seen == []
