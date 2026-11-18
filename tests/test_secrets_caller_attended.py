"""P10 (MC-1179, Ron 2026-11-17): a secret marked attended-only is refused to a
caller the SERVER cannot attribute to an attended session.

Before: `detect_effective_unattended` looked up the trigger type of a session id
the CALLER supplied (the exec routes' `claude_session_id` body field, or the
`CLAUDE_CODE_SESSION_ID` variable that a command-line prefix can set). An
unattended cycle that named a manual chat's id was classed attended and was
handed an `allow_unattended=false` secret. Now the server decides from the
OS attribution of the calling process (mc/caller_attribution.py, the one
/agent/dispatch and /agent/send use) and the trigger type it recorded for the
session that process descends from.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SECRET = 'PLAINTEXT-VALUE-SHOULD-NEVER-APPEAR'
PASSCODE = 'unlock1234'
PRINT_ENV_CMD = [sys.executable, '-c', 'import os, sys; sys.stdout.write(os.environ["X"])']


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import secrets_store
    from mc.blueprints import local_auth, secrets_caller_routes, secrets_routes
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    secrets_store._exec_token = None
    app = Flask(__name__)
    app.register_blueprint(secrets_routes.bp)
    app.register_blueprint(secrets_caller_routes.bp)
    return app


def _create(client, name='demo.token', **over):
    from mc.blueprints import local_auth
    local_auth._local_auth_set_passcode(PASSCODE)
    body = {'name': name, 'value': SECRET, 'passcode': PASSCODE}
    body.update(over)
    r = client.post('/api/secrets', json=body)
    assert r.status_code in (200, 201), r.get_data(as_text=True)


def _exec(client, **body):
    from mc import secrets_store as vault
    return client.post('/api/secrets/exec', json=body,
                       headers={'X-Clayrune-Exec-Token': vault.ensure_exec_token()})


def _attribute_as(monkeypatch, session_id, sessions):
    """Make the server's OS attribution say the caller descends from
    `session_id`, whatever the request body claims."""
    from mc import caller_attribution as ca
    from mc.state import agent_sessions
    agent_sessions.clear()
    agent_sessions.update(sessions)
    monkeypatch.setattr(
        ca, 'attribute_caller',
        lambda *a, **k: ca.Attribution(ca.ATTRIBUTED, session_id, 1, 'test'))
    return agent_sessions


@pytest.fixture(autouse=True)
def _clean_sessions():
    from mc.state import agent_sessions
    saved = dict(agent_sessions)
    yield
    agent_sessions.clear()
    agent_sessions.update(saved)


# ── the defect, at the route ────────────────────────────────────────────────

def test_exec_does_not_trust_a_session_id_the_caller_names(env, monkeypatch):
    """The body names a MANUAL chat's id; the process actually asking descends
    from a scheduled session. The old code looked the named id up and said
    attended."""
    client = env.test_client()
    _create(client, allow_unattended=False)
    _attribute_as(monkeypatch, 'sched-1', {
        'sched-1': {'session_id': 'sched-1', 'trigger_type': 'schedule',
                    'claude_session_id': 'csid-sched'},
        'chat-1': {'session_id': 'chat-1', 'trigger_type': 'manual',
                   'claude_session_id': 'csid-manual'}})
    from mc import secrets_store as vault
    monkeypatch.setattr(vault, '_lookup_trigger_type', lambda _sid: 'manual', raising=False)
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD,
                claude_session_id='csid-manual')
    assert res.status_code == 400, res.get_data(as_text=True)
    assert 'attended-only' in res.get_json()['error']
    assert SECRET not in res.get_data(as_text=True)


def test_exec_serves_a_caller_attributed_to_a_manual_session(env, monkeypatch):
    client = env.test_client()
    _create(client, allow_unattended=False)
    _attribute_as(monkeypatch, 'chat-1', {
        'chat-1': {'session_id': 'chat-1', 'trigger_type': 'manual'}})
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD)
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()['stdout'] == '[redacted:demo.token]'


def test_exec_serves_an_unattended_caller_a_secret_that_allows_it(env, monkeypatch):
    client = env.test_client()
    _create(client, allow_unattended=True)
    _attribute_as(monkeypatch, 'sched-1', {
        'sched-1': {'session_id': 'sched-1', 'trigger_type': 'schedule'}})
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD)
    assert res.status_code == 200, res.get_data(as_text=True)


@pytest.mark.parametrize('status', ['unattributed', 'unavailable'])
def test_exec_refuses_a_caller_it_cannot_attribute(env, monkeypatch, status):
    client = env.test_client()
    _create(client, allow_unattended=False)
    from mc import caller_attribution as ca
    monkeypatch.setattr(ca, 'attribute_caller',
                        lambda *a, **k: ca.Attribution(status, detail='x'))
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD,
                claude_session_id='csid-manual')
    assert res.status_code == 400
    assert 'attended-only' in res.get_json()['error']


def test_the_unattended_flag_still_only_adds_strictness(env, monkeypatch):
    client = env.test_client()
    _create(client, allow_unattended=False)
    _attribute_as(monkeypatch, 'chat-1', {
        'chat-1': {'session_id': 'chat-1', 'trigger_type': 'manual'}})
    res = _exec(client, env=[['X', 'demo.token']], command=PRINT_ENV_CMD, unattended=True)
    assert res.status_code == 400


# ── the decision ────────────────────────────────────────────────────────────

class _Req:
    remote_addr = '127.0.0.1'
    environ = {'REMOTE_PORT': '50000', 'SERVER_PORT': '5199'}


def _decide(monkeypatch, attribution, sessions):
    from mc import caller_attribution as ca
    from mc import secrets_caller_attended as sca
    monkeypatch.setattr(ca, 'attribute_caller', lambda *a, **k: attribution)
    return sca.caller_unattended(_Req(), sessions, {})


def test_decision_table(monkeypatch):
    from mc import caller_attribution as ca
    att = lambda sid: ca.Attribution(ca.ATTRIBUTED, sid, 1, 'd')
    assert _decide(monkeypatch, att('a'), {'a': {'session_id': 'a', 'trigger_type': 'manual'}})[0] is False
    assert _decide(monkeypatch, att('a'), {'a': {'session_id': 'a', 'trigger_type': 'schedule'}})[0] is True
    assert _decide(monkeypatch, att('a'), {'a': {'session_id': 'a', 'trigger_type': 'dispatch'}})[0] is True
    # a session record with no trigger type is not proof of an attended chat
    assert _decide(monkeypatch, att('a'), {'a': {'session_id': 'a'}})[0] is True
    # attributed to a session the server no longer holds
    assert _decide(monkeypatch, att('gone'), {})[0] is True
    assert _decide(monkeypatch, ca.Attribution(ca.UNATTRIBUTED), {})[0] is True
    assert _decide(monkeypatch, ca.Attribution(ca.UNAVAILABLE), {})[0] is True


def test_decision_never_raises(monkeypatch):
    from mc import caller_attribution as ca
    from mc import secrets_caller_attended as sca

    def boom(*a, **k):
        raise RuntimeError('x')
    monkeypatch.setattr(ca, 'attribute_caller', boom)
    assert sca.caller_unattended(_Req(), {}, {})[0] is True


# ── the in-process path, over a real socket ─────────────────────────────────

class _FakeProc:
    def __init__(self, pid):
        self.pid = pid

    def poll(self):
        return None


def _serve(app):
    from werkzeug.serving import make_server
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


@pytest.mark.parametrize('trigger, want_unattended', [('manual', False), ('schedule', True)])
def test_in_process_caller_is_classified_by_its_real_socket(env, monkeypatch, trigger,
                                                           want_unattended):
    """with-secret.py's in-process path: the vault client asks the server, which
    attributes the connecting process by its TCP peer port. This test process is
    the 'session' (its pid is the managed root), so the lookup is attributed
    for real, with no stub on the attribution."""
    pytest.importorskip('psutil')
    from mc import secrets_store as vault
    from mc.state import agent_sessions, tracked_processes
    agent_sessions.clear()
    agent_sessions['s1'] = {'session_id': 's1', 'trigger_type': trigger,
                            'proc': _FakeProc(os.getpid())}
    srv, t = _serve(env)
    try:
        monkeypatch.setenv('MC_PORT', str(srv.server_port))
        # The env var the old code trusted must change nothing.
        monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', 'csid-for-some-manual-chat')
        got, reason = vault.detect_effective_unattended(False)
    finally:
        srv.shutdown()
        t.join(timeout=2)
    assert got is want_unattended, reason


def test_in_process_caller_with_no_server_is_unattended(monkeypatch):
    from mc import secrets_store as vault
    monkeypatch.setenv('MC_PORT', '1')
    assert vault.detect_effective_unattended(False)[0] is True


def test_caller_endpoint_refuses_a_tunnel_request(env):
    res = env.test_client().get('/api/secrets/caller-unattended',
                                headers={'Cf-Ray': 'x'})
    assert res.status_code == 403
