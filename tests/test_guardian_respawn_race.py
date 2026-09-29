"""MC-1002: a working Mode B session vanishes from the Floor after a context
rollover (auto-fresh) or any other kill-old/spawn-new respawn.

Root cause: the respawn path (agent_routes.py's `_do_respawn_b` / Mode A's
`_start_followup`, and their three call sites) sets `session['status'] =
'running'` and then kills the OLD process before the NEW one is assigned to
`session['proc']` -- and the Popen for the new one (full context rebuild, MCP
fleet start) can take several seconds, well past the Guardian's dead-process
thresholds. A Guardian tick landing in that window sees a legitimately-dead
old proc under a 'running' status and (State 1 / State 7) flips status to
'error'; nothing ever flips it back once the new process comes up, so the
session reads as errored on /api/floor while the new process keeps working.

The fix: `_respawn_in_flight` is set on the session for the duration of that
handoff window (set alongside `status = 'running'` at every respawn call
site; cleared -- with status forced back to 'running' -- once the new proc is
assigned, or to 'error' on a genuine respawn failure) and is treated as the
same kind of safety-net exemption Guardian already gives
`waiting_for_question` / `waiting_for_plan_approval` / `evicted`. Both State 1
and State 7 also re-validate `session.get('proc') is proc` (identity) AFTER
acquiring the lock, so a respawn that completes between the Guardian's
unlocked read and its lock acquisition can't be overwritten either.
"""
import time

from mc.blueprints import agent_routes as ar


class _FakeProc:
    """Mimics subprocess.Popen just enough for the guardian's checks:
    `.pid` and `.poll()` (None while alive, an int once exited)."""

    def __init__(self, pid, alive=True, on_poll=None):
        self.pid = pid
        self._alive = alive
        self._on_poll = on_poll

    def poll(self):
        if self._on_poll:
            self._on_poll()
        return None if self._alive else 1


def _base_session(proc, status='running', last_change_ago=10):
    now = time.time()
    return {
        'status': status,
        'project_id': 'mc1002_test',
        'mode': 'B',
        'proc': proc,
        'process_alive': True,
        'log_lines': [],
        'last_output_time': now - last_change_ago,
        'last_status_change_time': now - last_change_ago,
        'recovery_attempts': 0,
    }


def _found_dead_lines(session):
    return [l for l in session['log_lines'] if 'found dead' in str(l)
            or 'process dead but status was running' in str(l)]


def test_respawn_in_flight_exempts_state1(monkeypatch):
    """Guardian tick lands while the old proc is dead and the new one hasn't
    been assigned yet (State 1's >2s window) -- must not flip to 'error'."""
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: False)
    old_proc = _FakeProc(pid=11111, alive=False)
    session = _base_session(old_proc, status='running', last_change_ago=10)
    session['_respawn_in_flight'] = time.time()

    ar._guardian_check_session('mc1002a', session, time.time())

    assert session['status'] == 'running'
    assert _found_dead_lines(session) == []


def test_respawn_in_flight_exempts_state7(monkeypatch):
    """Same handoff window, but past State 7's 15s threshold with no proc
    assigned at all (the Popen-hasn't-returned-yet case)."""
    session = _base_session(None, status='running', last_change_ago=20)
    session['_respawn_in_flight'] = time.time()

    ar._guardian_check_session('mc1002b', session, time.time())

    assert session['status'] == 'running'
    assert _found_dead_lines(session) == []


def test_respawn_completes_during_guardian_tick_stays_running(monkeypatch):
    """The exact MC-1002 interleaving: the Guardian's unlocked read captures
    the dying old proc, but by the time it acquires the lock, `_do_respawn_b`
    has already finished -- new proc assigned, `_respawn_in_flight` cleared,
    status forced back to 'running'. The proc-identity re-check under the
    lock must refuse to stamp 'error' over that."""
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: False)
    session = _base_session(None, status='running', last_change_ago=10)
    old_proc = _FakeProc(pid=22222, alive=False)
    session['proc'] = old_proc
    session['_respawn_in_flight'] = time.time()

    new_proc = _FakeProc(pid=33333, alive=True)

    def _respawn_lands_concurrently():
        # Simulates _do_respawn_b's success path completing between the
        # guardian's initial (unlocked) dead-proc check and its lock
        # acquisition a few lines later.
        session['proc'] = new_proc
        session.pop('_respawn_in_flight', None)
        session['status'] = 'running'

    old_proc._on_poll = _respawn_lands_concurrently

    ar._guardian_check_session('mc1002c', session, time.time())

    assert session['status'] == 'running'
    assert session['proc'] is new_proc
    assert _found_dead_lines(session) == []


def test_genuine_dead_process_without_respawn_flag_still_errors(monkeypatch):
    """Negative control: a real crash (no rollover/respawn in flight) must
    still be caught -- the exemption is scoped to the handoff window, not a
    blanket guardian disable."""
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: False)
    old_proc = _FakeProc(pid=44444, alive=False)
    session = _base_session(old_proc, status='running', last_change_ago=10)
    # No `_respawn_in_flight` -- this session isn't mid-handoff.

    ar._guardian_check_session('mc1002d', session, time.time())

    assert session['status'] == 'error'
    assert len(_found_dead_lines(session)) == 1


def test_genuine_dead_process_state7_without_respawn_flag_still_errors():
    """Negative control for State 7 (no proc at all, > 15s, no in-flight
    respawn) -- a real Popen failure must still be recovered to 'error'."""
    session = _base_session(None, status='running', last_change_ago=20)

    ar._guardian_check_session('mc1002e', session, time.time())

    assert session['status'] == 'error'
    assert len(_found_dead_lines(session)) == 1


def test_stale_respawn_flag_does_not_blind_guardian(monkeypatch):
    """A handoff flag left set past _RESPAWN_WINDOW_MAX_S (a path that forgot
    to clear it) must not hide a genuinely dead session forever."""
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: False)
    session = _base_session(None, status='running', last_change_ago=20)
    session['_respawn_in_flight'] = time.time() - ar._RESPAWN_WINDOW_MAX_S - 5

    ar._guardian_check_session('mc1002s', session, time.time())

    assert session['status'] == 'error'
