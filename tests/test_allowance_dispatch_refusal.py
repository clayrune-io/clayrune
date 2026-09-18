"""VENDOR_AGNOSTIC_PROGRAM.md §4 item 3: dispatch/follow-up refusal.

Ron's bar: allowance is the ONLY reason an installed, signed-in agent may be
refused, and there is never a silent fallback to another vendor. These tests
prove the refusal fires at the two real entry points — _dispatch_agent_internal
(shared by the HTTP dispatch endpoint, the scheduler, hivemind and every
workflow agent step) and agent_followup — before any process is spawned.
"""
from pathlib import Path

import pytest

from mc import allowance_state as al
from tests.test_revive_notify_carry import ar, _project


@pytest.fixture(autouse=True)
def _isolated_allowance_state(tmp_path):
    al.wire(tmp_path / 'allowance_state.json')
    yield
    al._STATE = {}


def test_dispatch_refuses_when_provider_is_exhausted(ar, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    al.record_exhaustion('claude', limit_kind='five_hour',
                          resets_at_display='Sep 24, 2026 7:58 AM')

    with pytest.raises(ValueError) as exc:
        ar._dispatch_agent_internal('p1', 'do something', provider_override='claude')

    msg = str(exc.value)
    assert 'claude' in msg
    assert 'Sep 24, 2026 7:58 AM' in msg
    assert 'no fallback' in msg


def test_dispatch_check_is_a_noop_when_provider_not_exhausted(ar):
    """Negative control for the check inserted in _dispatch_agent_internal
    (`_allowance_state.refusal_message(provider_name)` right after
    engine_selection.resolve_provider) — asserted directly rather than by
    driving the full dispatch (which spawns a real subprocess) so this test
    cannot itself launch a process."""
    assert ar._allowance_state.refusal_message('claude') == ''


def test_followup_refuses_when_session_provider_is_exhausted(ar, monkeypatch):
    session = {'project_id': 'p1', 'session_id': 's1', 'provider': 'codex',
               'log_lines': [], 'status': 'idle'}
    ar.agent_sessions['s1'] = session
    monkeypatch.setattr(ar, 'load_project',
                        lambda _: {'id': 'p1', 'project_path': '/tmp/proj'})
    monkeypatch.setattr(Path, 'is_dir', lambda self: True)
    al.record_exhaustion('codex', limit_kind='usage_limit',
                          resets_at_display='Sep 24, 2026 7:58 AM')

    import server  # app already wired by the `ar` fixture's own import
    with server.app.test_client() as c:
        resp = c.post('/api/project/p1/agent/followup',
                      json={'message': 'still there?', 'session_id': 's1'})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body['allowance_exhausted'] is True
    assert body['vendor'] == 'codex'
    assert 'Sep 24, 2026 7:58 AM' in body['error']
