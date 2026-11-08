"""MC-1165 round 5 follow-up: the agent-log row is the durable record that a
conversation was isolated. A purged isolated session whose worktree AND branch
are gone (merged and deleted) must still revive into a rebuilt worktree, or be
refused; it must never fall back to the project checkout. Legacy rows with no
flag keep the git-state fallback, and a vendor session's dead-process guardian
paths hold its request instead of retrying through Claude."""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import pytest
from mc import agent_worktree as w, isolated_launch as launch
from mc.state import agent_sessions
from test_cold_review_wiring import project, routes
from test_merge_review_gate import repo, _git  # noqa: F401  (repo: fixture chain)
from test_mc1165_review4_probes import revive_setup, _no_leaked_revived_sessions  # noqa: F401


def _log_rows(routes, monkeypatch, rows):
    """Run agent-log mutators against an in-memory list."""
    monkeypatch.setattr(routes, '_update_agent_log', lambda pid, mutate: mutate(rows))
    return rows



# ── the flag is written and survives ─────────────────────────────────────────

def test_pending_row_records_isolation(routes, monkeypatch):
    rows = _log_rows(routes, monkeypatch, [])
    routes._log_agent_dispatch_pending(
        {'project_id': 'p', 'session_id': 's1', 'task': 't', '_worktree_isolated': True},
        write_turn_start=False)
    assert rows[0]['worktree_isolated'] is True


def test_pending_row_of_an_ordinary_session_has_no_flag(routes, monkeypatch):
    rows = _log_rows(routes, monkeypatch, [])
    routes._log_agent_dispatch_pending(
        {'project_id': 'p', 'session_id': 's2', 'task': 't'}, write_turn_start=False)
    assert 'worktree_isolated' not in rows[0]


def test_flag_survives_a_re_dispatch_from_a_session_dict_that_lost_it(routes, monkeypatch):
    rows = _log_rows(routes, monkeypatch, [])
    routes._log_agent_dispatch_pending(
        {'project_id': 'p', 'session_id': 's3', 'task': 't', '_worktree_isolated': True},
        write_turn_start=False)
    routes._log_agent_dispatch_pending(
        {'project_id': 'p', 'session_id': 's3', 'task': 't'}, write_turn_start=False)
    assert rows[0]['worktree_isolated'] is True


def test_completion_row_keeps_the_flag(routes, monkeypatch):
    rows = _log_rows(routes, monkeypatch, [])
    monkeypatch.setattr(routes, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(routes, '_notify_agent_spawner', lambda *a, **k: None)
    sess = {'project_id': 'p', 'session_id': 's4', 'task': 't', 'status': 'completed',
            'log_lines': [], '_worktree_isolated': True}
    routes._log_agent_dispatch_pending(dict(sess, status='running'), write_turn_start=False)
    # The completing session dict is rebuilt after a restart and no longer knows.
    routes._log_agent_completion(dict(sess, _worktree_isolated=False))
    done = [r for r in rows if r['session_id'] == 's4']
    assert len(done) == 1 and done[0]['status'] != 'in_progress'
    assert done[0]['worktree_isolated'] is True


# ── revival reads it ─────────────────────────────────────────────────────────

def test_owned_worktree_trusts_the_logged_flag_with_no_git_state(project):
    assert launch.owned_worktree(project, 'never-had-one') == ''
    path = launch.owned_worktree(project, 'never-had-one', logged=True)
    assert path == str(w.worktree_path(project, 'never-had-one'))


@pytest.mark.parametrize('streaming', [False, True])
def test_logged_isolation_revives_into_a_rebuilt_worktree(routes, project, monkeypatch, streaming):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=True)
    _git(project['project_path'], 'branch', '-D', w.branch_name(sid))
    assert launch.owned_worktree(project, sid) == ''      # git alone has no record
    entry = routes._load_agent_log('testproj')[0]
    entry['worktree_isolated'] = True
    session = routes._revive_from_agent_log('testproj', sid, 'continue', project)
    assert spawned == [str(w.worktree_path(project, sid))]
    assert spawned[0] != project['project_path']
    assert session['_worktree_isolated'] and session['_agent_cwd'] == spawned[0]
    assert Path(spawned[0]).is_dir()


@pytest.mark.parametrize('streaming', [False, True])
def test_logged_isolation_refuses_rather_than_use_main(routes, project, monkeypatch, streaming):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=True)
    _git(project['project_path'], 'branch', '-D', w.branch_name(sid))
    routes._load_agent_log('testproj')[0]['worktree_isolated'] = True
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    assert routes._revive_from_agent_log('testproj', sid, 'continue', project) is None
    assert not spawned and sid not in agent_sessions


@pytest.mark.parametrize('streaming', [False, True])
def test_legacy_row_without_the_flag_is_the_disclosed_residual(routes, project, monkeypatch, streaming):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=True)
    _git(project['project_path'], 'branch', '-D', w.branch_name(sid))
    session = routes._revive_from_agent_log('testproj', sid, 'continue', project)
    assert spawned == [project['project_path']] and not session['_worktree_isolated']


# ── vendor sessions at the dead-process guardian states ──────────────────────

def _vendor_session(sid, state_status):
    return {'project_id': 'testproj', 'session_id': sid, 'provider': 'codex',
            'status': state_status, 'mode': 'B', 'log_lines': [], 'task': 't',
            'pending_recovery_message': 'continue my Codex task',
            'last_status_change_time': 0, 'last_output_time': 0,
            'proc': SimpleNamespace(pid=999999, poll=lambda: 1), 'process_alive': True}


@pytest.mark.parametrize('status', ['running', 'idle'])
def test_dead_process_states_hold_a_vendor_request(routes, project, monkeypatch, status):
    sid = f'codex-dead-{status}'
    session = _vendor_session(sid, status)
    monkeypatch.setitem(agent_sessions, sid, session)
    monkeypatch.setattr(routes, '_pid_is_alive', lambda pid: False)
    calls = []
    monkeypatch.setattr(routes, '_auto_dispatch_followup', lambda *a, **k: calls.append(a))
    routes._guardian_check_session(sid, session, 10_000)
    assert not calls, f'vendor request retried through the Claude follow-up: {calls}'
    assert session['status'] == 'error'
    assert session['guardian_state'] == 'needs_attention'
    assert session['pending_recovery_message'] == 'continue my Codex task'


def test_try_again_on_a_vendor_session_stays_held(routes, monkeypatch):
    session = _vendor_session('codex-retry', 'error')
    calls = []
    monkeypatch.setattr(routes, '_auto_dispatch_followup', lambda *a, **k: calls.append(a))
    routes._guardian_attempt_recovery(session)
    assert not calls and session['guardian_state'] == 'needs_attention'


def test_claude_recovery_is_unchanged(routes, monkeypatch):
    session = _vendor_session('claude-retry', 'error')
    session.update(provider='claude', recovery_attempts=0, last_recovery_time=0, proc=None)
    calls = []
    monkeypatch.setattr(routes, '_auto_dispatch_followup', lambda *a, **k: calls.append(a))
    routes._guardian_attempt_recovery(session)
    assert len(calls) == 1 and session['recovery_attempts'] == 1
