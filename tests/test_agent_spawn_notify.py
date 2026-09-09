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
