"""MC-946 — a dispatched agent must be able to wake its spawner.

The failure this pins is behavioural, not a crash: an agent that dispatches
another agent over HTTP used to get no signal when the child finished, so it
would say "I'll report back when it returns" and then go permanently quiet.
Nothing errored; the promise simply had no trigger behind it. These tests hold
the three pieces that make the trigger real — the id is accepted, it is stored
on the session, and completion delivers a message back.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc.state as state  # noqa: E402

state.CONFIG.setdefault('port', 5199)

from mc.blueprints import agent_routes as ar  # noqa: E402


def test_dispatch_accepts_notify_session_kwarg():
    """The plumbing is only real if the internal entry point takes it."""
    import inspect
    sig = inspect.signature(ar._dispatch_agent_internal)
    assert 'notify_session' in sig.parameters
    # Optional, so every existing caller (scheduler, hivemind, revive) keeps
    # working untouched — a required arg here would break all of them.
    assert sig.parameters['notify_session'].default == ''


def test_roster_tells_agents_the_callback_exists():
    """A capability no agent is told about is a capability nobody uses.

    Dave reached for the in-process Agent tool specifically because he believed
    HTTP dispatch could not notify him. The roster block is where that belief
    is formed, so it has to carry the correction.
    """
    text = ar._roster_block({'id': 'mission_control'}, 5199, 'sess-abc-123')
    assert 'notify_session' in text
    # The agent's OWN id is interpolated — telling it the parameter exists
    # without telling it what to put there is what leaves it guessing.
    assert 'sess-abc-123' in text


def test_completion_notifies_spawner(monkeypatch):
    sent = {}

    def fake_notify(project_id, notify_sid, child, summary):
        sent['project_id'] = project_id
        sent['notify_sid'] = notify_sid
        sent['summary'] = summary

    monkeypatch.setattr(ar, '_notify_agent_spawner', fake_notify)
    session = {
        'project_id': 'mission_control',
        'session_id': 'child-1',
        '_notify_session': 'parent-1',
        'status': 'completed',
        'task': 'run the tests',
        'log_lines': ['hello', 'all 14 tests passed'],
    }
    try:
        ar._log_agent_completion(session)
    except Exception:
        # Completion does plenty besides this hook (agent_log, memory, telemetry)
        # and any of it may fail in a bare test env. The notification fires
        # before that work, so the assertion below is still the real check.
        pass
    assert sent.get('notify_sid') == 'parent-1'
    assert sent.get('project_id') == 'mission_control'


def test_no_self_notify_and_no_notify_when_unset(monkeypatch):
    """Two ways this could misfire: notifying nobody, or notifying itself.

    Self-notification would be a live agent posting a message into its own
    chat at completion — a loop, and the kind that looks like the agent has
    started talking to itself.
    """
    calls = []
    monkeypatch.setattr(ar, '_notify_agent_spawner',
                        lambda *a, **k: calls.append(a))

    for sess in (
        {'project_id': 'p', 'session_id': 's1', 'status': 'completed',
         'log_lines': ['x']},                      # no _notify_session at all
        {'project_id': 'p', 'session_id': 's1', '_notify_session': 's1',
         'status': 'completed', 'log_lines': ['x']},  # points at itself
    ):
        try:
            ar._log_agent_completion(dict(sess))
        except Exception:
            pass
    assert calls == []


def test_incognito_child_does_not_notify(monkeypatch):
    """Incognito is a promise that the session leaves no trace in MC.

    A callback carrying its task and final answer into another chat would be
    exactly such a trace, so the existing incognito early-return must keep
    winning over the notification.
    """
    calls = []
    monkeypatch.setattr(ar, '_notify_agent_spawner',
                        lambda *a, **k: calls.append(a))
    try:
        ar._log_agent_completion({
            'project_id': 'p', 'session_id': 'c1', '_notify_session': 'parent-1',
            'incognito': True, 'status': 'completed', 'log_lines': ['secret'],
        })
    except Exception:
        pass
    assert calls == []


def test_notify_fires_once_and_is_latched(monkeypatch):
    """Mode B calls this at the turn boundary AND again at process exit.

    Without the latch a dispatched child would post its result into the
    spawner's chat twice — and the second copy arrives whenever the process
    happens to die, which reads as the agent repeating itself for no reason.
    """
    calls = []
    monkeypatch.setattr(ar, '_notify_agent_spawner',
                        lambda *a, **k: calls.append(a))
    sess = {'project_id': 'p', 'session_id': 'child',
            '_notify_session': 'parent', 'log_lines': ['the answer is 4']}
    ar._maybe_notify_spawner(sess, 'the answer is 4')
    ar._maybe_notify_spawner(sess, 'the answer is 4')
    assert len(calls) == 1
    assert sess['_notify_sent'] is True


def test_last_reply_text_skips_status_lines_and_the_task_seed():
    """MC-935 in miniature: handing back the task instead of the answer."""
    sess = {'log_lines': [
        '> Ron: run the tests',          # dispatcher's seed
        'all 14 tests passed',           # the real answer
        '[exited with code 0]',          # MC status line
    ]}
    assert ar._last_reply_text(sess) == 'all 14 tests passed'
    assert ar._last_reply_text({'log_lines': ['> Ron: x', '[status]']}) == ''


def test_callback_names_the_agent_not_its_record(monkeypatch):
    """A live session's `character` is a dict, not a string.

    The first real callback (2026-09-09) opened with the entire character
    record -- scope, engine, avatar, model -- where "Tobin" belonged.
    """
    captured = {}

    class _FakeThread:
        def __init__(self, target=None, **kw):
            self._t = target

        def start(self):
            self._t()

    class _Resp:
        def read(self):
            return b''

    def _fake_urlopen(req, timeout=None):
        captured['body'] = req.data.decode()
        return _Resp()

    import urllib.request as u
    # The sender runs on a thread; run it inline so the assertion is not a race.
    monkeypatch.setattr(ar.threading, 'Thread', _FakeThread)
    monkeypatch.setattr(u, 'urlopen', _fake_urlopen)

    ar._notify_agent_spawner('p', 'parent', {
        'session_id': 'child',
        'character': {'agent_name': 'Tobin', 'display_name': 'builder',
                      'engine': {'model': 'claude-sonnet-5'}},
        'status': 'completed', 'task': 't',
    }, '4')
    assert 'Tobin' in captured.get('body', '')
    assert 'claude-sonnet-5' not in captured.get('body', '')
