"""MC-1165 round 5: Fenn's review-4 repros, kept as regression tests, plus the
unit tests for the helpers that fix them. Temporary repos, process creation
faked, no provider launched. Pins: a refused vendor launch is never retried
through the Claude-only guardian; a revived isolated conversation keeps its
isolation, starts inside the lifecycle window and never in the main checkout;
a restored directory with a dead `.git` marker is not a usable worktree."""
import sys
import threading
import subprocess
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import pytest
from mc import agent_worktree as w, isolated_launch as launch, worktree_lifecycle as lifecycle
from mc.state import agent_sessions
from test_cold_review_wiring import project, routes, _session
from test_merge_review_gate import _agent, _store, _register, repo, _git
import test_worktree_lifecycle as supplied


@pytest.fixture(autouse=True)
def _no_leaked_revived_sessions():
    """A revival publishes its session straight into the module-level registry."""
    yield
    for sid in [k for k in agent_sessions if k.startswith(('revive-', 'codex-refusal'))]:
        agent_sessions.pop(sid, None)


@pytest.mark.parametrize('recorded', [True, False])
def test_stale_git_marker_is_refused(routes, project, monkeypatch, recorded):
    sid = 'stale-marker'
    tree, _ = _agent(project, sid)
    tree = Path(tree)
    marker = (tree / '.git').read_text()
    assert w.remove(project, sid, delete_branch=False, force=True)[0]
    tree.mkdir()
    (tree / '.git').write_text(marker)
    git = subprocess.run(['git', 'status', '--porcelain'], cwd=tree, capture_output=True, text=True)
    assert git.returncode != 0 and 'not a git repository' in git.stderr
    session = _session(sid)
    session.update(status='error', mode='A', log_lines=[], proc=None,
                   _agent_cwd=str(tree) if recorded else '')
    supplied._fake_spawn(routes, monkeypatch)
    spawned = []
    monkeypatch.setattr(routes, 'spawn_engine_proc', lambda *a, **k:
                        spawned.append(k['cwd']) or SimpleNamespace(pid=12345))
    routes._auto_dispatch_followup(session, 'continue')
    assert not spawned, f'spawned in stale worktree: {spawned}; git error={git.stderr.strip()}'
    assert session['pending_recovery_message'] == 'continue'


def revive_setup(routes, project, monkeypatch, streaming, removed=False):
    from mc import state, session_provenance
    sid = 'revive-b' if streaming else 'revive-a'
    tree, _ = _agent(project, sid)
    if removed:
        assert w.remove(project, sid, delete_branch=False, force=True)[0]
    project['use_streaming_agent'] = streaming
    entry = {'session_id': sid, 'claude_session_id': 'prior-native-id',
             'provider': 'claude', 'source': 'agent', 'task': 'finish the change'}
    monkeypatch.setattr(routes, '_load_agent_log', lambda *a: [entry])
    # Revival now claims durable delivery ownership before spawn. Keep writes
    # paired with the same in-memory history as this fixture's mocked reads.
    monkeypatch.setattr(routes, '_update_agent_log', lambda pid, mutate: mutate(routes._load_agent_log(pid)))
    supplied._fake_spawn(routes, monkeypatch)
    for name in ('_assert_runtime_project_generation', '_refuse_duplicate_spawn',
                 '_prior_character', '_read_agent_stream_b'):
        monkeypatch.setattr(routes, name, lambda *a, **k: None)
    monkeypatch.setattr(routes, '_auto_fresh_trigger', lambda *a, **k: (None, 0))
    monkeypatch.setattr(routes, '_build_agent_context', lambda *a, **k: 'CTX')
    monkeypatch.setattr(routes, '_revive_history_lines', lambda *a, **k: ([], []))
    monkeypatch.setattr(routes, '_sysprompt_file_args', lambda *a: ([], None))
    monkeypatch.setattr(routes._memory_turn, 'seed_delivered', lambda *a: None)
    monkeypatch.setattr(session_provenance, 'note_resume', lambda *a: None)
    monkeypatch.setattr(state, 'CONFIG', {'agent_revive_from_log': True})
    # Use the REAL _resume_cwd_for, with only the provider transcript store faked.
    runtime = SimpleNamespace(transcript_path=lambda cwd, native:
                              Path(tree) / 'saved-transcript' if Path(cwd) == Path(tree) else None)
    monkeypatch.setattr(routes._agent_runtime, 'get_runtime', lambda *a: runtime)
    manager = routes.get_manager('testproj')
    monkeypatch.setattr(manager, 'ensure_guardian', lambda: None)
    spawned = []
    event = threading.Event()
    def spawn(*a, **kw):
        spawned.append(kw['cwd'])
        event.set()
        return SimpleNamespace(pid=12345, stdin=SimpleNamespace(write=lambda s: None, flush=lambda: None))
    monkeypatch.setattr(routes, 'spawn_engine_proc', spawn)
    return sid, tree, spawned, event


@pytest.mark.parametrize('streaming', [False, True])
def test_revive_removed_isolated_tree_never_uses_main(routes, project, monkeypatch, streaming):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=True)
    session = routes._revive_from_agent_log('testproj', sid, 'continue', project)
    assert spawned and spawned[0] != project['project_path'], (
        f'revival spawned in MAIN: {spawned}; status={session and session.get("status")}; '
        f'isolated={session and session.get("_worktree_isolated")}')


@pytest.mark.parametrize('streaming', [False, True])
def test_revive_keeps_isolation_identity(routes, project, monkeypatch, streaming):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming)
    session = routes._revive_from_agent_log('testproj', sid, 'continue', project)
    assert spawned == [tree]
    assert session.get('_worktree_isolated'), (
        f'revived worktree session dropped isolation: cwd={session["_agent_cwd"]}')


@pytest.mark.parametrize('streaming', [False, True])
def test_revive_waits_for_removal_window(routes, project, monkeypatch, streaming):
    sid, tree, spawned, event = revive_setup(routes, project, monkeypatch, streaming)
    result, errors = [], []
    def run():
        try:
            result.append(routes._revive_from_agent_log('testproj', sid, 'continue', project))
        except Exception as e:
            errors.append(e)
    t = threading.Thread(target=run)
    with lifecycle.removal_window(routes.get_manager('testproj').lock, sid):
        t.start()
        inside = event.wait(1)
    t.join(10)
    assert not t.is_alive() and not errors and result
    assert not inside, f'revival spawned during removal window: {spawned}'


def test_refused_vendor_followup_never_retries_as_claude(routes, project, monkeypatch):
    sid = 'codex-refusal'
    tree, _ = _agent(project, sid)
    session = _session(sid)
    session.update(status='completed', mode='A', proc=None, log_lines=[],
                   provider='codex', provider_session_id='codex-native', _agent_cwd=tree)
    monkeypatch.setitem(agent_sessions, sid, session)
    assert w.remove(project, sid, delete_branch=False, force=True)[0]
    real_create = w.create
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'temporary recreation failure'))
    # The exact helper called by the non-Claude HTTP follow-up/interrupt paths.
    with pytest.raises(launch.LaunchRefused):
        routes._followup_cwd_for(session, project, project['project_path'], 'continue my Codex task')
    assert session['pending_recovery_message'] == 'continue my Codex task'
    monkeypatch.setattr(w, 'create', real_create)
    supplied._fake_spawn(routes, monkeypatch)
    spawns = []
    monkeypatch.setattr(routes, 'spawn_engine_proc', lambda cmd, **kw:
                        spawns.append((cmd, kw['engine'], kw['cwd'])) or SimpleNamespace(pid=12345))
    routes._guardian_check_session(sid, session, session['last_status_change_time'] + 11)
    assert not any(engine == 'claude' for cmd, engine, cwd in spawns), (
        f'Codex refusal retried through Claude: {spawns}; '
        f'provider={session["provider"]}; status={session["status"]}; '
        f'pending={session.get("pending_recovery_message")}')


# ── unit tests for the helpers ───────────────────────────────────────────────

def test_valid_worktree_accepts_a_live_worktree_and_rejects_the_rest(project, tmp_path):
    tree, _ = _agent(project, 'live1')
    base = project['project_path']
    assert w.valid_worktree(base, tree) == (True, '')
    # An enclosing repo's subfolder is not a worktree of its own.
    sub = Path(base) / 'subdir'
    sub.mkdir()
    ok, why = w.valid_worktree(base, sub)
    assert not ok and 'not a worktree of its own' in why
    # A worktree of a DIFFERENT repository is refused.
    other = tmp_path / 'other'
    other.mkdir()
    subprocess.run(['git', 'init', '-q', str(other)], check=True)
    ok, why = w.valid_worktree(base, other)
    assert not ok and 'project repository' in why


def test_create_refuses_a_stale_marker_instead_of_returning_it(project):
    tree, _ = _agent(project, 'stale-create')
    tree = Path(tree)
    marker = (tree / '.git').read_text()
    assert w.remove(project, 'stale-create', delete_branch=False, force=True)[0]
    tree.mkdir()
    (tree / '.git').write_text(marker)
    ok, msg = w.create(project, 'stale-create')
    assert not ok and 'not a usable worktree' in msg


def test_owned_worktree_reads_tree_then_branch_then_nothing(project):
    base = project['project_path']
    assert launch.owned_worktree(project, 'never-had-one') == ''
    tree, _ = _agent(project, 'owner1')
    assert launch.owned_worktree(project, 'owner1') == str(tree)
    assert w.remove(project, 'owner1', delete_branch=False, force=True)[0]
    assert launch.owned_worktree(project, 'owner1') == str(tree)          # branch kept
    _git(base, 'branch', '-D', w.branch_name('owner1'))
    assert launch.owned_worktree(project, 'owner1') == ''                  # no record left


@pytest.mark.parametrize('provider,held', [('codex', True), ('gemini', True), ('qwen', True),
                                           ('claude', False), ('', False), (None, False)])
def test_hold_vendor_recovery_only_holds_non_claude(provider, held):
    session = {'provider': provider, 'log_lines': [], 'pending_recovery_message': 'm'}
    assert launch.hold_vendor_recovery(session) is held
    assert (session.get('guardian_state') == 'needs_attention') is held
    if held:
        assert launch.hold_vendor_recovery(session) is True
        assert len(session['log_lines']) == 1       # the explanation is written once
        assert session['pending_recovery_message'] == 'm'


@pytest.mark.parametrize('streaming', [False, True])
def test_revive_refused_when_the_tree_cannot_be_rebuilt(routes, project, monkeypatch, streaming):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=True)
    monkeypatch.setattr(w, 'create', lambda *a, **k: (False, 'no disk'))
    # r6: a typed refusal, not None -- None is "nothing to revive" and the callers
    # answer that with a fresh dispatch in the project checkout.
    with pytest.raises(launch.RevivalRefused, match='no disk'):
        routes._revive_from_agent_log('testproj', sid, 'continue', project)
    assert not spawned and sid not in agent_sessions


@pytest.mark.parametrize('streaming', [False, True])
def test_revive_with_no_record_of_isolation_is_an_ordinary_session(routes, project, monkeypatch, streaming):
    sid, tree, spawned, _ = revive_setup(routes, project, monkeypatch, streaming, removed=True)
    _git(project['project_path'], 'branch', '-D', w.branch_name(sid))
    session = routes._revive_from_agent_log('testproj', sid, 'continue', project)
    assert spawned == [project['project_path']] and not session['_worktree_isolated']
