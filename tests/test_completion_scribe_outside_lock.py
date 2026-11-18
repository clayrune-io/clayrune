"""R38 (MC-1179, docs/_review/2026-11-16_02_agent_routes.md): the Scribe's model
call ran while the project lock was held.

`_log_agent_completion_body` ends in `_write_session_memory` (the Scribe: one
provider call per transcript chunk). Its callers hold `get_manager(pid).lock`
when they call it, so a long Scribe pinned every other request for the project.
The Scribe step is now deferred to `mc/completion_scribe.py` and run after the
caller releases the lock.

Pinned here: the lock is NOT held while the (fake) model call runs; and when two
turns of one project finish together, both Scribes are in flight at once (they
would deadlock on the barrier if the lock serialised them) and each turn's log
row, `scribed` marker and summary still end up right. No real model call.
"""
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from test_runtime_completion_log import env, _dispatch, _log_rows  # noqa: E402,F401
from mc import completion_scribe  # noqa: E402


def _lock_free_from_other_thread(lock):
    """True when a DIFFERENT thread can take `lock` right now."""
    got = []

    def probe():
        ok = lock.acquire(timeout=1.0)
        got.append(ok)
        if ok:
            lock.release()

    t = threading.Thread(target=probe)
    t.start()
    t.join(5)
    return bool(got and got[0])


def test_project_lock_is_not_held_while_the_scribe_model_call_runs(env):
    ar = env['ar']
    seen = []

    def scribe(p, session, status, summary, ts_date):
        seen.append(_lock_free_from_other_thread(ar.get_manager('proj1').lock))
        return True

    ar._write_session_memory = scribe
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['the reply'])

    assert seen == [True], 'the Scribe ran while the project lock was held (or never ran)'
    rows = _log_rows(env)
    assert len(rows) == 1 and rows[0]['scribed'] is True and rows[0]['summary'] == 'the reply'


def test_two_turns_of_one_project_finishing_together_keep_their_own_row_and_summary(env):
    ar = env['ar']
    barrier = threading.Barrier(2, timeout=5)
    calls = {}
    broken = []

    def scribe(p, session, status, summary, ts_date):
        calls[session['session_id']] = summary
        try:
            barrier.wait()  # both Scribes must be in flight at once
        except threading.BrokenBarrierError:
            broken.append(session['session_id'])
        return True

    ar._write_session_memory = scribe
    sid_a, handle_a = _dispatch(env, task='turn a')
    sid_b, handle_b = _dispatch(env, task='turn b')

    threads = [
        threading.Thread(target=env['runtime'].run_turn, args=(handle_a, ['reply A'])),
        threading.Thread(target=env['runtime'].run_turn, args=(handle_b, ['reply B'])),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)

    assert not broken, 'the two Scribes were serialised: the project lock was held across the model call'
    assert calls == {sid_a: 'reply A', sid_b: 'reply B'}
    rows = {r['session_id']: r for r in _log_rows(env)}
    assert set(rows) == {sid_a, sid_b}
    assert rows[sid_a]['summary'] == 'reply A' and rows[sid_a]['scribed'] is True
    assert rows[sid_b]['summary'] == 'reply B' and rows[sid_b]['scribed'] is True


def test_caller_that_never_called_begin_still_runs_the_step_inline():
    ran = []
    assert completion_scribe.run_or_defer(lambda: ran.append(1)) is False
    assert ran == [1]


def test_flush_runs_queued_steps_once_and_survives_a_failing_step():
    ran, logged = [], []
    completion_scribe.begin()
    assert completion_scribe.run_or_defer(lambda: ran.append('first')) is True
    assert completion_scribe.run_or_defer(lambda: (_ for _ in ()).throw(RuntimeError('boom'))) is True
    assert completion_scribe.run_or_defer(lambda: ran.append('third')) is True
    assert ran == []
    completion_scribe.flush(logged.append)
    assert ran == ['first', 'third']
    assert len(logged) == 1 and 'boom' in logged[0]
    completion_scribe.flush(logged.append)  # second flush: nothing left, no re-run
    assert ran == ['first', 'third']


# ── nesting + a skipped flush (Dave's hardening request) ─────────────────────

def test_nested_window_keeps_and_runs_the_outer_step_exactly_once_after_the_outer_flush():
    ran = []
    completion_scribe.begin()
    completion_scribe.run_or_defer(lambda: ran.append('outer'))
    completion_scribe.begin()                       # inner window on the same thread
    completion_scribe.run_or_defer(lambda: ran.append('inner'))
    completion_scribe.flush(print)                  # inner close: nothing runs yet
    assert ran == []
    completion_scribe.run_or_defer(lambda: ran.append('outer-2'))  # still deferring
    assert ran == []
    completion_scribe.flush(print)                  # outermost close runs all, once
    assert ran == ['outer', 'inner', 'outer-2']
    completion_scribe.flush(print)
    assert ran == ['outer', 'inner', 'outer-2']


def test_flush_without_a_matching_begin_is_a_noop_and_does_not_break_the_next_window():
    ran = []
    completion_scribe.flush(print)
    completion_scribe.flush(print)
    completion_scribe.begin()
    completion_scribe.run_or_defer(lambda: ran.append('x'))
    assert ran == []
    completion_scribe.flush(print)
    assert ran == ['x']


def test_a_caller_that_raises_inside_the_window_still_gets_its_step_run():
    ran, logged = [], []
    try:
        with completion_scribe.window(logged.append):
            completion_scribe.run_or_defer(lambda: ran.append('scribed'))
            raise RuntimeError('caller blew up before its own flush')
    except RuntimeError:
        pass
    assert ran == ['scribed'], 'the step queued before the raise was lost'
    completion_scribe.run_or_defer(lambda: ran.append('inline'))   # window closed: inline again
    assert ran == ['scribed', 'inline']


def test_window_inside_window_does_not_discard_the_outer_turns_step(env):
    ar = env['ar']
    scribed = []
    ar._write_session_memory = lambda p, session, status, summary, ts: scribed.append(summary) or True
    sid, handle = _dispatch(env)
    with completion_scribe.window(print):
        env['runtime'].run_turn(handle, ['outer turn reply'])   # its own window nests inside
        assert scribed == []                                    # deferred to the outer close
    assert scribed == ['outer turn reply']
