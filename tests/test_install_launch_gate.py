"""MC-1030: the provider install/update routes are human-only.

POST /api/agent/provider/<name>/install-launch and
POST /api/agent/providers/install-launch run a software install in a terminal,
so they sit behind the retyped dashboard passcode (`_require_human_passcode`,
the same gate as the secrets and attend-once routes). What gets launched is
covered in tests/test_agent_routes.py; this file covers who may ask.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PASSCODE = 'install-gate-1234'

SINGLE = '/api/agent/provider/codex/install-launch'
BATCH = '/api/agent/providers/install-launch'


class _Health:
    installed = False
    binary_path = None
    version = None
    auth_state = None
    install_hint = 'npm install -g @openai/codex'
    update_hint = ''


class _Runtime:
    def health_check(self):
        return _Health()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'auth.json')
    monkeypatch.setattr(la, '_LOCAL_AUTH_FAILS', {})
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: _Runtime())
    monkeypatch.setattr(ar.shutil, 'which', lambda name, *a, **k: '/x/' + name)
    monkeypatch.setattr(ar, '_npm_major_version', lambda _bin: 11)
    monkeypatch.setattr(ar, '_read_powershell_execution_scopes',
                        lambda: {'CurrentUser': 'RemoteSigned'})
    launched = []
    monkeypatch.setattr(ar, '_launch_install_terminal',
                        lambda command: (launched.append(command) or 'sess-gate', None))
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client(), launched, la
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


CASES = [
    pytest.param(SINGLE, {}, id='single'),
    pytest.param(BATCH, {'names': ['codex']}, id='batch'),
]


@pytest.mark.parametrize('path,body', CASES)
def test_refused_when_no_passcode_is_configured(env, path, body):
    client, launched, _la = env
    resp = client.post(path, json=dict(body, passcode='whatever'))
    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'passcode_required'
    assert launched == []


@pytest.mark.parametrize('path,body', CASES)
def test_refused_without_a_passcode(env, path, body):
    client, launched, la = env
    la._local_auth_set_passcode(PASSCODE)
    resp = client.post(path, json=body)
    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'bad_passcode'
    assert launched == []


@pytest.mark.parametrize('path,body', CASES)
def test_refused_with_a_wrong_passcode(env, path, body):
    client, launched, la = env
    la._local_auth_set_passcode(PASSCODE)
    resp = client.post(path, json=dict(body, passcode='not-the-real-one'))
    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'bad_passcode'
    assert launched == []


@pytest.mark.parametrize('path,body', CASES)
def test_forged_origin_alone_does_not_pass(env, path, body):
    client, launched, la = env
    la._local_auth_set_passcode(PASSCODE)
    resp = client.post(path, json=body, headers={'Origin': 'http://localhost:5199'})
    assert resp.status_code == 403
    assert launched == []


@pytest.mark.parametrize('path,body', CASES)
def test_launches_with_the_right_passcode(env, path, body):
    client, launched, la = env
    la._local_auth_set_passcode(PASSCODE)
    resp = client.post(path, json=dict(body, passcode=PASSCODE))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['ok'] is True
    assert data['session_id'] == 'sess-gate'
    assert len(launched) == 1
    assert 'npm install -g' in launched[0] and '@openai/codex' in launched[0]


def test_update_flag_still_gated(env):
    client, launched, la = env
    la._local_auth_set_passcode(PASSCODE)
    resp = client.post(SINGLE, json={'update': True})
    assert resp.status_code == 403
    assert launched == []
