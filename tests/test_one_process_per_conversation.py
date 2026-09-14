"""One live claude process per conversation (2026-09-14).

REPRODUCED LIVE 2026-09-14: one Dave chat (claude_session_id 3f6c790f) ran as
FOUR simultaneous `claude -r` processes, each in a brand-new agent worktree,
one per message sent. The chat tab was keyed on the CLAUDE session id (the
transcript-reconstruct view), so /agent/send found no session under that id
and fell through to its csid-resume branch, which dispatched a fresh `-r` even
though an idle Mode B session already owned the conversation with a live
process. The copies appended to one transcript and forked it: replies vanished
from the screen and messages went unanswered.

The route tests drive the real Flask routes; `subprocess.Popen` is patched to
fail, so any second spawn fails the test instead of starting a process.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CSID = '3f6c790f-9ec2-4ded-9793-970a6a2f340b'


class _Stdin:
    def __init__(self):
        self.written = []

    def write(self, s):
        self.written.append(s)

    def flush(self):
        pass

    def close(self):
        pass


class _Proc:
    def __init__(self, alive=True, pid=424242):
        self.alive = alive
        self.pid = pid
        self.stdin = _Stdin()

    def poll(self):
        return None if self.alive else 0


def _session(sid, csid=CSID, alive=True, status='idle', last_output=0.0):
    return {
        'session_id': sid, 'project_id': 'p1', 'claude_session_id': csid,
        'status': status, 'task': 'chat', 'log_lines': ['> Ron: hi', 'hello'],
        'started_at': '2026-09-14T20:00:00Z', 'mode': 'B', 'proc': _Proc(alive),
        'process_alive': alive, 'stdin_lock': threading.Lock(),
        'last_output_time': last_output, 'last_status_change_time': 0.0,
    }


def _fail(what):
    def _f(*a, **k):
        raise AssertionError(f'{what} must not run for a conversation another session owns')
    return _f


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setitem(mc_state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(mc_state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setattr(ar.subprocess, 'Popen', _fail('subprocess.Popen'))

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield {'client': server.app.test_client(), 'ar': ar,
               'sessions': mc_state.agent_sessions, 'project': project,
               'pp': project_path}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def test_send_addressed_by_csid_goes_to_idle_owner(env, monkeypatch):
    """The exact live repro: a message POSTed with session_id=<claude csid>
    while an idle Mode B session holds that conversation."""
    ar = env['ar']
    owner = _session('5f399332c751')
    env['sessions']['5f399332c751'] = owner
    monkeypatch.setattr(ar, '_dispatch_agent_internal', _fail('dispatch'))
    monkeypatch.setattr(ar, '_revive_from_agent_log', _fail('revive'))

    resp = env['client'].post('/api/project/p1/agent/send', json={
        'session_id': CSID, 'message': 'the split view never actually worked'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()['session_id'] == '5f399332c751'
    assert _wait(lambda: owner['proc'].stdin.written), 'message never reached the live process'
    assert 'the split view never actually worked' in owner['proc'].stdin.written[0]
    assert set(env['sessions']) == {'5f399332c751'}, 'a second session was registered'


def test_send_from_stale_mc_tab_goes_to_owner(env, monkeypatch):
    """An old tab (the original 28aff8341987) whose agent_log row carries the
    csid a newer session resumed must not revive a second process."""
    ar = env['ar']
    owner = _session('0be6a5d88147')
    env['sessions']['0be6a5d88147'] = owner
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [
        {'session_id': '28aff8341987', 'claude_session_id': CSID}])
    monkeypatch.setattr(ar, '_dispatch_agent_internal', _fail('dispatch'))
    monkeypatch.setattr(ar, '_revive_from_agent_log', _fail('revive'))

    resp = env['client'].post('/api/project/p1/agent/send', json={
        'session_id': '28aff8341987', 'message': 'still there?'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()['session_id'] == '0be6a5d88147'
    assert _wait(lambda: owner['proc'].stdin.written)


def test_send_to_dead_session_goes_to_live_copy(env, monkeypatch):
    """A registered session whose process exited, while another session runs
    the same conversation: the message goes to the live copy, not a respawn."""
    ar = env['ar']
    dead = _session('953fd4139ed6', alive=False)
    live = _session('ee6e4f9c4788')
    env['sessions'].update({'953fd4139ed6': dead, 'ee6e4f9c4788': live})
    monkeypatch.setattr(ar, '_dispatch_agent_internal', _fail('dispatch'))

    resp = env['client'].post('/api/project/p1/agent/send', json={
        'session_id': '953fd4139ed6', 'message': 'hello?'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()['session_id'] == 'ee6e4f9c4788'
    assert _wait(lambda: live['proc'].stdin.written)


def test_owner_resolution_prefers_live_then_most_recent(env):
    ar = env['ar']
    env['sessions']['a'] = _session('a', alive=False, last_output=99.0)
    env['sessions']['b'] = _session('b', alive=True, last_output=1.0)
    env['sessions']['c'] = _session('c', alive=True, last_output=5.0)
    assert ar._resolve_conversation_owner('p1', CSID) == 'c'
    assert ar._resolve_conversation_owner('p1', 'b') is None, 'a live session owns itself'
    assert ar._resolve_conversation_owner('p1', 'unknown-id') is None


def test_dispatch_refuses_second_process_before_worktree(env, monkeypatch):
    ar = env['ar']
    env['sessions']['5f399332c751'] = _session('5f399332c751')
    monkeypatch.setattr(ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (None, ''))
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', _fail('worktree creation'))

    with pytest.raises(ValueError, match='already running in session 5f399332c751'):
        ar._dispatch_agent_internal('p1', 'hello', resume_id=CSID)


def test_revive_refuses_when_conversation_live_elsewhere(env, monkeypatch):
    """Records spawn ATTEMPTS: revive swallows a Popen exception and returns
    None, so a raising Popen alone would pass on the unfixed code too."""
    ar = env['ar']
    env['sessions']['5f399332c751'] = _session('5f399332c751')
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [
        {'session_id': '28aff8341987', 'claude_session_id': CSID}])
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    attempts = []

    def _record(cmd, *a, **k):
        attempts.append(cmd)
        raise OSError('spawn blocked by test')

    monkeypatch.setattr(ar.subprocess, 'Popen', _record)
    assert ar._revive_from_agent_log('p1', '28aff8341987', 'hi', env['project']) is None
    assert attempts == [], f'revive tried to spawn a second process: {attempts[:1]}'


def test_guard_ignores_own_session(env):
    ar = env['ar']
    env['sessions']['a'] = _session('a')
    assert ar._refuse_duplicate_spawn('p1', CSID, 'a', 'respawn-B') is None
    env['sessions']['b'] = _session('b')
    assert ar._refuse_duplicate_spawn('p1', CSID, 'a', 'respawn-B') == 'b'
    env['sessions']['b']['proc'].alive = False
    assert ar._refuse_duplicate_spawn('p1', CSID, 'a', 'respawn-B') is None


def test_followup_respawn_refused_when_another_copy_is_live(env, monkeypatch):
    """/agent/followup straight at a dead session (bypassing /send's routing)
    still must not start a second process: the respawn thread refuses and says
    where the conversation is running."""
    ar = env['ar']
    dead = _session('a', alive=False)
    env['sessions'].update({'a': dead, 'b': _session('b')})
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *a, **k: ([], None))
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(ar, '_kill_proc_background', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_unregister_process', lambda *a, **k: None)

    resp = env['client'].post('/api/project/p1/agent/followup', json={
        'session_id': 'a', 'message': 'hi'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _wait(lambda: any('already running in session b' in l for l in dead['log_lines'])), \
        dead['log_lines']
    assert dead['status'] == 'idle'
    assert dead['process_alive'] is False


def test_status_reports_live_copies_and_moves(env):
    env['sessions']['a'] = _session('a', last_output=2.0)
    env['sessions']['b'] = _session('b', last_output=1.0)
    env['sessions']['c'] = _session('c', alive=False)
    env['sessions']['c']['_cwd_moved_from'] = '/gone/tree'
    env['sessions']['solo'] = _session('solo', csid='other-conversation')

    resp = env['client'].get('/api/project/p1/agent/status')

    assert resp.status_code == 200, resp.get_data(as_text=True)
    rows = {r['session_id']: r for r in resp.get_json()['sessions']}
    assert rows['a']['live_copies'] == ['b']
    assert rows['b']['live_copies'] == ['a']
    assert sorted(rows['c']['live_copies']) == ['a', 'b']
    assert rows['c']['cwd_moved_from'] == '/gone/tree'
    assert rows['solo']['live_copies'] == []


def test_resume_cwd_is_the_transcript_tree(env, monkeypatch):
    ar = env['ar']
    pp = env['pp']
    wt = pp / '.clayrune' / 'agents' / '28aff8341987'
    wt.mkdir(parents=True)

    class _RT:
        def transcript_path(self, path, csid):
            return str(Path(path) / 't.jsonl') if Path(path) == wt and csid == CSID else None

    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: _RT())
    assert ar._resume_cwd_for(str(pp), CSID) == str(wt)
    assert ar._resume_cwd_for(str(pp), 'no-such-conversation') is None


def test_session_cwd_keeps_worktree_and_records_move(env):
    ar = env['ar']
    pp = env['pp']
    wt = pp / '.clayrune' / 'agents' / 'abc'
    wt.mkdir(parents=True)
    kept = {'session_id': 'x', '_agent_cwd': str(wt)}
    assert ar._session_cwd(kept, str(pp)) == str(wt)
    assert '_cwd_moved_from' not in kept

    gone = {'session_id': 'y', '_agent_cwd': str(pp / '.clayrune' / 'agents' / 'gone')}
    assert ar._session_cwd(gone, str(pp)) == str(pp)
    assert gone['_cwd_moved_from'].endswith('gone')
