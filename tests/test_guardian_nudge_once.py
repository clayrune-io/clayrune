"""The Guardian's "question/plan approval may have been missed" nudge fires
once per stuck episode, not on every guardian tick (Ron 2026-09-29: his chat
filled with copies of the same line)."""
import time

from mc.blueprints import agent_routes as ar


def _stuck_session(flag):
    now = time.time()
    return {
        'status': 'waiting', 'project_id': 'nudge_test', 'mode': 'B',
        'proc': None, 'log_lines': [],
        'last_output_time': now - 10_000,
        'last_status_change_time': now - 10_000,
        '_last_sse_poll_time': 0,
        flag: True,
    }


def _count(session, text):
    return sum(1 for l in session['log_lines'] if text in str(l))


def _tick(session, monkeypatch, n=5):
    monkeypatch.setattr(ar, '_should_evict_idle_session', lambda *a, **k: False)
    now = time.time()
    for i in range(n):
        ar._guardian_check_session('nudgetest0000', session, now + i * 30)


def test_question_nudge_appears_once_per_episode(monkeypatch):
    s = _stuck_session('waiting_for_question')
    _tick(s, monkeypatch)
    assert _count(s, 'question may have been missed') == 1
    # A status change starts a new episode, which may nudge once more.
    s['last_status_change_time'] = time.time() - 5_000
    _tick(s, monkeypatch)
    assert _count(s, 'question may have been missed') == 2


def test_plan_nudge_appears_once_per_episode(monkeypatch):
    s = _stuck_session('waiting_for_plan_approval')
    _tick(s, monkeypatch)
    assert _count(s, 'plan approval may have been missed') == 1
