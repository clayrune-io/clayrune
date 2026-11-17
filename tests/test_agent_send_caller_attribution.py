"""MC-1179 R48: POST .../agent/send must attribute its caller like /agent/dispatch.

`agent_dispatch` overrides the body's hints with OS-level attribution
(`mc/caller_attribution.py`) so a session another agent spawned is never
stamped 'manual'. `agent_send` reached the same `_dispatch_agent_internal`
twice (fresh path, native-resume path) with no `trigger_type` and no `source`,
so the dispatcher's 'manual' default applied: a managed agent that POSTed
`/agent/send` with no live session_id got a child that the fence, the secrets
vault and the config gate all read as attended.

These drive the real `agent_send` handler with `_dispatch_agent_internal`
stubbed, so the assertion is on the exact `trigger_type`/`source` the child
would be launched with. The caller rig is the one test_caller_attribution.py
uses (fake psutil, no real sockets).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import caller_attribution as ca  # noqa: E402
from tests.test_agent_routes import client  # noqa: E402,F401  (Flask test client rig)
from tests.test_caller_attribution import (  # noqa: E402,F401
    CHAIN, CURL_PID, OWN_PID, PEER, PEER_PORT, SERVER_PORT,
    FakePsutil, _agent_caller, _conn, _isolated_process_tracker, _register_session,
)

UUID = '0190aaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
ORIGIN = {'Origin': 'http://localhost:5199'}


@pytest.fixture()
def rig(client, tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar
    pp = tmp_path / 'proj'
    pp.mkdir()
    project = {'id': 'p1', 'project_path': str(pp), 'provider': 'claude'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_mark_human_joined', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_observe_habit_cue', lambda *a, **k: None)
    captured = {}
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *a, **kw: captured.update(kw) or 'sid-x')
    return SimpleNamespace(client=client, ar=ar, captured=captured, project=project)


def _native_codex(monkeypatch, ar):
    """Let the native-resume branch (provider_session_id) run to its dispatch."""
    runtime = SimpleNamespace(transcript_path=lambda pp, nid: Path(pp) / 't.jsonl',
                              extract_chat_turns=lambda tpath: [])
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: runtime)
    monkeypatch.setattr(ar._agent_runtime, '_codex_read_meta', lambda tpath: ('cwd', UUID))
    monkeypatch.setattr(ar._agent_runtime, '_codex_same_path', lambda a, b: True)


FRESH = pytest.param({'json': {'message': 'hi'}, 'headers': {}}, id='fresh')
FRESH_SPOOF_UI = pytest.param(
    {'json': {'message': 'hi', 'source': 'ui', 'client': 'mobile'}, 'headers': ORIGIN},
    id='fresh-spoofing-ui')
NATIVE = pytest.param(
    {'json': {'message': 'hi', 'provider': 'codex', 'provider_session_id': UUID},
     'headers': ORIGIN}, id='native-resume')


@pytest.mark.parametrize('req', [FRESH, FRESH_SPOOF_UI, NATIVE])
def test_attributed_caller_cannot_start_an_attended_child_via_send(rig, monkeypatch, req):
    _native_codex(monkeypatch, rig.ar)
    _agent_caller(monkeypatch)
    resp = rig.client.post('/api/project/p1/agent/send', json=req['json'],
                           headers=req['headers'], environ_overrides=PEER)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert rig.captured['trigger_type'] == 'dispatch'
    assert rig.captured['source'] == 'agent'


@pytest.mark.parametrize('req', [FRESH_SPOOF_UI, NATIVE])
def test_ui_caller_that_is_not_under_a_session_stays_manual(rig, monkeypatch, req):
    """Control: the chat composer (Origin present, not a session descendant)
    keeps today's attended 'manual' child."""
    _native_codex(monkeypatch, rig.ar)
    _register_session(monkeypatch)
    ps = FakePsutil(conns=[_conn(PEER_PORT, SERVER_PORT, 7000)], parents={7000: [6000, 1]})
    monkeypatch.setattr(ca, '_load_psutil', lambda: ps)
    resp = rig.client.post('/api/project/p1/agent/send', json=req['json'],
                           headers=req['headers'], environ_overrides=PEER)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    # _dispatch_agent_internal's own defaults are trigger_type='manual', source=''.
    assert rig.captured.get('trigger_type', 'manual') == 'manual'
    # /agent/send has never honoured a body `source`; an unattributed caller
    # keeps exactly that.
    assert rig.captured.get('source', '') == ''


def test_lookup_failure_without_origin_fails_closed_via_send(rig, monkeypatch):
    _register_session(monkeypatch)
    monkeypatch.setattr(ca, '_load_psutil', lambda: None)      # psutil unavailable
    resp = rig.client.post('/api/project/p1/agent/send', json={'message': 'hi'},
                           environ_overrides=PEER)
    assert resp.status_code == 200
    assert rig.captured['trigger_type'] == 'dispatch'
    assert rig.captured['source'] == 'agent'


def test_send_to_a_live_session_does_not_run_attribution(rig, monkeypatch):
    """Attribution is only for the paths that start a child. A message to a
    session that exists is a followup and must not pay for a socket-table scan."""
    called = []
    monkeypatch.setattr(ca, 'resolve_dispatch', lambda *a, **k: called.append(1) or ('x', 'y'))
    monkeypatch.setattr(rig.ar, 'agent_followup',
                        lambda pid: rig.ar.jsonify({'ok': True}))
    from mc import state as mc_state
    mc_state.agent_sessions['live-1'] = {
        'session_id': 'live-1', 'project_id': 'p1', 'status': 'idle',
        'log_lines': [], 'proc': SimpleNamespace(pid=1, poll=lambda: None)}
    resp = rig.client.post('/api/project/p1/agent/send',
                           json={'message': 'hi', 'session_id': 'live-1'},
                           headers=ORIGIN, environ_overrides=PEER)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert called == []
