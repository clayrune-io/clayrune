"""Regression for the 2026-10-15 stuck chat (cad3d, MC session 2d54c95f3c0e).

A mid-turn rollover spawned a fresh child; its SessionStart hooks outlasted the
20 s stdin watchdog, so it was killed before it read the message. The CLI
emits `system/hook_*` events carrying its brand-new session_id BEFORE it reads
stdin, and the reader committed that id. It never got a transcript, so every
follow-up ran `claude -r <id>` and died with "No conversation found with
session ID".

Half (a): the reader must not adopt an id from a pre-stdin hook event.
Half (b): `-r` against an id with no transcript must take the rollover
handoff path (carrying the newest earlier transcript, or starting fresh with
the persona context) instead of spawning `-r`.
Plus: the stdin watchdog gives a still-talking startup a bounded grace period.

Never spawns a real model CLI.
"""
import io
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import resume_target as rt  # noqa: E402

GOOD = '36b474b3-3bf6-4bec-b28a-ae81cdb9953c'
POISON = 'd8f301c4-1aa3-4fe4-8017-82c8f1ceb9d0'
OLDER = '2f82e237-53a3-4e25-97c8-beabf9756be3'


# ── module units ────────────────────────────────────────────────────────────

def test_hook_events_are_startup_events():
    assert rt.is_startup_hook_event({'type': 'system', 'subtype': 'hook_started'})
    assert rt.is_startup_hook_event({'type': 'system', 'subtype': 'hook_progress'})
    assert rt.is_startup_hook_event({'type': 'system', 'subtype': 'hook_response'})
    assert not rt.is_startup_hook_event({'type': 'system', 'subtype': 'init'})
    assert not rt.is_startup_hook_event({'type': 'result', 'session_id': 'x'})
    assert not rt.is_startup_hook_event('hook_started')


def test_no_conversation_id_matches_only_the_cli_error():
    line = f'No conversation found with session ID: {POISON}'
    assert rt.no_conversation_id(line) == POISON
    envelope = ('{"type":"result","is_error":true,"errors":["No conversation '
                f'found with session ID: {POISON}"]}}')
    assert rt.no_conversation_id(envelope) == POISON
    # An assistant message QUOTING the error (this exact text gets pasted into
    # chats) must not poison the live id.
    quoted = ('{"type":"assistant","message":{"content":[{"type":"text","text":'
              f'"it said No conversation found with session ID: {POISON}"}}]}}}}')
    assert rt.no_conversation_id(quoted) is None
    assert rt.no_conversation_id(f'> Ron: {line}') is None
    assert rt.no_conversation_id('all good') is None


def test_assess_ok_when_transcript_exists_touches_nothing():
    s = {'session_id': 's1'}
    assert rt.assess_resume_target(s, GOOD, [GOOD], lambda sid: sid == GOOD) is False
    assert '_rolled_from' not in s


def test_assess_missing_transcript_carries_newest_earlier_id():
    s = {'session_id': 's1', '_rolled_from': OLDER}
    logs = []
    unres = rt.assess_resume_target(
        s, POISON, [OLDER, GOOD, POISON], lambda sid: sid in (OLDER, GOOD), log=logs.append)
    assert unres is True
    assert s['_rolled_from'] == GOOD  # newest with a transcript, not the stale one
    assert any('[resume-heal]' in l and POISON[:12] in l and GOOD[:12] in l for l in logs), logs


def test_assess_no_transcript_anywhere_clears_rolled_from():
    s = {'session_id': 's1', '_rolled_from': OLDER}
    assert rt.assess_resume_target(s, POISON, [OLDER, POISON], lambda sid: False) is True
    assert '_rolled_from' not in s


def test_assess_cli_rejected_id_is_unresumable_even_if_file_exists():
    s = {'session_id': 's1', '_unresumable_sids': [GOOD]}
    assert rt.assess_resume_target(s, GOOD, [OLDER, GOOD], lambda sid: True) is True
    assert s['_rolled_from'] == OLDER


def test_observe_child_line_stamps_and_records_rejection():
    s = {'session_id': 's1'}

    class P:
        pid = 77

    rt.observe_child_line(s, P, 'hello', now=100.0)
    assert s['_child_line_at'] == {'pid': 77, 'at': 100.0}
    assert '_unresumable_sids' not in s
    rt.observe_child_line(s, P, f'No conversation found with session ID: {POISON}')
    rt.observe_child_line(s, P, f'No conversation found with session ID: {POISON}')
    assert s['_unresumable_sids'] == [POISON]


def test_startup_grace_only_for_a_talking_child_and_is_capped():
    class P:
        pid = 5

    class Other:
        pid = 6

    s = {'_child_line_at': {'pid': 5, 'at': 1000.0}}
    assert rt.startup_grace_applies(s, P, 990.0, 20.0, now=1010.0) is True
    assert rt.startup_grace_applies(s, P, 990.0, 20.0, now=1021.0) is False   # silent 21 s
    assert rt.startup_grace_applies(s, Other, 990.0, 20.0, now=1010.0) is False  # old child's line
    assert rt.startup_grace_applies(None, P, 990.0, 20.0, now=1010.0) is False
    assert rt.startup_grace_applies({}, P, 990.0, 20.0, now=1010.0) is False
    assert rt.startup_grace_applies(s, P, 1010.0 - rt.STARTUP_GRACE_MAX_S, 20.0,
                                    now=1010.0) is False  # hard cap


# ── (a) reader must not adopt a pre-stdin hook event's id ───────────────────

class _FakeProc:
    def __init__(self, lines):
        self.stdout = io.StringIO('\n'.join(lines) + '\n')
        self.pid = -1
        self.stdin = None

    def wait(self):
        return 0

    def kill(self):
        pass


def _reader_session():
    return {'project_id': 'p-resume', 'status': 'running', 'log_lines': [],
            'last_output_time': 0.0, 'last_status_change_time': 0.0,
            'provider': 'claude', 'housekeeping': True, 'session_id': 'sess1'}


@pytest.mark.parametrize('reader', ['_read_agent_stream', '_read_agent_stream_b'])
def test_reader_ignores_session_id_from_hook_events(tmp_data_dir, reader):
    import server  # noqa: F401  (wires agent_routes)
    from mc.blueprints import agent_routes as ar

    hooks = ['{"type":"system","subtype":"hook_started","session_id":"%s"}' % POISON,
             '{"type":"system","subtype":"hook_progress","session_id":"%s"}' % POISON]
    s = _reader_session()
    s['claude_session_id'] = GOOD
    proc = _FakeProc(hooks)
    s['proc'] = proc
    getattr(ar, reader)(proc, s)
    assert s['claude_session_id'] == GOOD, 'a hook event committed an unwritten id'
    assert not s.get('_claude_transcript_history')

    # `init` is emitted only after the CLI drained stdin: that one IS adopted.
    s2 = _reader_session()
    s2['claude_session_id'] = GOOD
    proc2 = _FakeProc(hooks + ['{"type":"system","subtype":"init","session_id":"%s"}' % POISON])
    s2['proc'] = proc2
    getattr(ar, reader)(proc2, s2)
    assert s2['claude_session_id'] == POISON


def test_reader_records_cli_rejection(tmp_data_dir):
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar

    s = _reader_session()
    s['claude_session_id'] = POISON
    proc = _FakeProc([f'No conversation found with session ID: {POISON}'])
    s['proc'] = proc
    ar._read_agent_stream_b(proc, s)
    assert s['_unresumable_sids'] == [POISON]


# ── (b) follow-up on a poisoned id takes the handoff path ───────────────────

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


def _dead_session(csid):
    return {
        'session_id': 'c1a2b3', 'project_id': 'p1', 'claude_session_id': csid,
        'status': 'idle', 'task': 'chat', 'log_lines': ['> Ron: hi', 'hello'],
        'started_at': '2026-10-15T20:00:00Z', 'mode': 'B', 'proc': _Proc(False),
        'process_alive': False, 'stdin_lock': threading.Lock(),
        'last_output_time': 0.0, 'last_status_change_time': 0.0,
        'context_tokens': None,
        'character': {'name': 'dave', 'scope': 'global'},
    }


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.delegation_delivery import DeliveryStore
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}
    transcripts = set()
    fresh_calls = []
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delegation.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [
        {'session_id': 'c1a2b3', 'claude_session_ids': [OLDER, GOOD, POISON],
         'claude_session_id': POISON}])
    monkeypatch.setattr(ar, '_find_transcript_file',
                        lambda pp, sid: (Path(pp) / f'{sid}.jsonl') if sid in transcripts else None)
    monkeypatch.setattr(ar, '_build_handoff_context',
                        lambda pp, prov, sid, project_id=None: (
                            (f'HANDOFF FROM {sid}', {}) if sid in transcripts
                            else (_ for _ in ()).throw(ValueError('no transcript'))))
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setitem(mc_state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(mc_state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 200_000)
    monkeypatch.setattr(ar, '_session_too_large', lambda pp, sid: (False, 4096))
    monkeypatch.setattr(ar, '_kill_proc_background', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_unregister_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_fresh_context_for',
                        lambda p, sess, task: fresh_calls.append(sess) or 'FRESH CONTEXT')
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda *a, **k: ([], None))
    monkeypatch.setattr(ar, '_log_agent_activity', lambda pid, line: None)

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield {'client': server.app.test_client(), 'ar': ar, 'sessions': mc_state.agent_sessions,
               'transcripts': transcripts, 'fresh_calls': fresh_calls}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _followup(env, monkeypatch, csid):
    ar = env['ar']
    live = _dead_session(csid)
    env['sessions']['c1a2b3'] = live
    new_proc = _Proc(pid=999999)
    spawned = []
    monkeypatch.setattr(ar.subprocess, 'Popen',
                        lambda cmd, **kw: spawned.append(cmd) or new_proc)
    resp = env['client'].post('/api/project/p1/agent/followup',
                              json={'session_id': 'c1a2b3', 'message': 'keep going'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _wait(lambda: bool(spawned) and bool(new_proc.stdin.written)), 'nothing spawned/sent'
    return live, spawned[0], new_proc.stdin.written[0]


def test_followup_on_healthy_id_still_resumes(env, monkeypatch):
    env['transcripts'].update({GOOD, POISON})
    _live, cmd, _sent = _followup(env, monkeypatch, POISON)
    assert cmd[:3] == ['claude', '-r', POISON]


def test_followup_on_transcriptless_id_carries_newest_earlier_transcript(env, monkeypatch):
    """The cad3d incident: claude_session_id points at an id that never wrote a
    transcript. Before the fix this spawned `claude -r d8f301c4...`."""
    env['transcripts'].update({OLDER, GOOD})
    live, cmd, sent = _followup(env, monkeypatch, POISON)
    assert '-r' not in cmd, f'spawned a resume against an unwritten id: {cmd}'
    assert f'HANDOFF FROM {GOOD}' in sent, sent
    assert any('interrupted' in l or 'recovering the real handoff' in l
               for l in live['log_lines']), live['log_lines']


def test_followup_with_no_transcript_anywhere_starts_fresh_with_persona(env, monkeypatch):
    live, cmd, sent = _followup(env, monkeypatch, POISON)
    assert '-r' not in cmd
    assert 'Real handoff unavailable' in sent
    # persona survives: context rebuilt from the live session, not bare
    assert env['fresh_calls'] and env['fresh_calls'][-1] is live
    assert env['fresh_calls'][-1]['character']['name'] == 'dave'


def test_followup_after_cli_rejection_never_resumes_the_rejected_id(env, monkeypatch):
    env['transcripts'].update({OLDER, GOOD, POISON})
    ar = env['ar']
    live = _dead_session(POISON)
    live['_unresumable_sids'] = [POISON]
    env['sessions']['c1a2b3'] = live
    new_proc = _Proc(pid=999998)
    spawned = []
    monkeypatch.setattr(ar.subprocess, 'Popen',
                        lambda cmd, **kw: spawned.append(cmd) or new_proc)
    resp = env['client'].post('/api/project/p1/agent/followup',
                              json={'session_id': 'c1a2b3', 'message': 'go'})
    assert resp.status_code == 200
    assert _wait(lambda: bool(spawned) and bool(new_proc.stdin.written))
    assert '-r' not in spawned[0]
    # The file is real, so the handoff reads it (rather than a fallback id).
    assert f'HANDOFF FROM {POISON}' in new_proc.stdin.written[0]


# ── stdin watchdog grace ────────────────────────────────────────────────────

class _BlockedStdin:
    """write() blocks until the process is 'killed' (pipe breaks)."""

    def __init__(self):
        self.broken = threading.Event()

    def write(self, s):
        self.broken.wait(5)
        raise BrokenPipeError('pipe broken')

    def flush(self):
        pass

    def close(self):
        pass


class _BlockedProc:
    pid = 31337

    def __init__(self):
        self.stdin = _BlockedStdin()


def _watchdog_run(monkeypatch, talking):
    import server  # noqa: F401
    from mc.blueprints import agent_routes as ar

    killed = threading.Event()
    monkeypatch.setattr(ar, '_kill_proc_background', lambda p: (killed.set(), p.stdin.broken.set()))
    proc = _BlockedProc()
    session = {'session_id': 'w1', 'log_lines': []}
    if talking:
        session['_child_line_at'] = {'pid': proc.pid, 'at': time.time()}
    ar._bounded_stdin_write(proc, 'x', session=session, timeout=0.3, context='test')
    return killed, session, proc


def test_watchdog_kills_a_silent_child_at_the_timeout(monkeypatch):
    killed, _s, _p = _watchdog_run(monkeypatch, talking=False)
    assert killed.wait(2.0), 'a child that printed nothing must still be killed'


def test_watchdog_spares_a_child_that_is_still_talking(monkeypatch):
    killed, session, proc = _watchdog_run(monkeypatch, talking=True)
    stop = threading.Event()

    def _talk():  # a child printing hook_progress every 50 ms
        while not stop.is_set():
            session['_child_line_at'] = {'pid': proc.pid, 'at': time.time()}
            time.sleep(0.05)

    t = threading.Thread(target=_talk, daemon=True)
    t.start()
    time.sleep(1.0)  # > 3x the 0.3 s timeout
    assert not killed.is_set(), 'killed a child that was still emitting startup output'
    stop.set()
    t.join()
    assert killed.wait(3.0), 'grace must end once the child goes silent'
