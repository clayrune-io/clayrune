"""Real pull enrollment routes with a synthetic signed fence service."""
import json
from types import SimpleNamespace

import pytest

from mc import state
from mc.agent_mail_service_client import FENCE
from mc.blueprints import agent_mail_enrollment_routes as routes
from test_agent_mail_enrollment_routes import rig  # noqa: F401

OP = 'f' * 32


def fence_service(rig, *, offline=False):
    calls = []

    def transport(method, path, headers, raw):
        if path != FENCE:
            return rig.h.transport(method, path, headers, raw)
        assert method == 'POST'
        value = json.loads(raw)
        calls.append(value)
        if offline:
            raise OSError('synthetic connection loss')
        install = value['install_id']
        return rig.h.response(
            dict(v=1, code='fenced', request_id=value['op_id'], install_id=install,
                 generation=1, state='fenced', fence_receipt='e' * 32),
            {'x-mail-install-id': install, 'x-mail-nonce': value['nonce']},
            path, overrides={'owner-generation': '0'},
        )

    rig.mail._pull_transport = transport
    return calls


@pytest.mark.parametrize('offline', [False, True])
def test_human_can_reach_pull_disable_while_unattended_agent_runs(rig, monkeypatch, offline):
    calls = fence_service(rig, offline=offline)
    rig.start()
    rig.verify()
    assert rig.post('enable').json['state'] == 'active'
    stopped = []

    def stop():
        with rig.mail.require_pull_store()._read() as db:
            assert db.execute('SELECT local_enabled FROM mail_pull_accounts').fetchone()[0] == 0
        stopped.append(True)
        return True

    monkeypatch.setattr(routes._supervisor, 'get', lambda: SimpleNamespace(factory=rig.mail, stop=stop))
    monkeypatch.setitem(state.agent_sessions, 'scheduled', {'status': 'running', 'trigger_type': 'schedule'})
    response = rig.post('disable', op_id=OP)
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    assert response.json['protocol'] == 'pull' and response.json['state'] == 'disabled'
    assert response.json['enabled'] is False and response.json['revocation_pending'] is offline
    assert len(calls) == 1 and calls[0]['mode'] == 'revoke' and calls[0]['op_id'] == OP
    assert stopped == [True]
    assert calls[0]['fence_token'] not in response.get_data(as_text=True)
    with rig.mail.require_pull_store()._read() as db:
        assert db.execute('SELECT enabled FROM registrations').fetchone()[0] == 0
    monkeypatch.setattr(routes._supervisor, 'get', lambda: None)
    status = rig.client.get(routes.BASE).json['pull_status']
    assert status['state'] == 'disabled' and status['revocation_pending'] is offline
    assert rig.post('disable', op_id=OP).status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize('body,status', [({'op_id': OP, 'passcode': 'forged'}, 403),
                                       ({'passcode': 'human-fixture'}, 400),
                                       ({'op_id': OP, 'install_id': 'a' * 32, 'passcode': 'human-fixture'}, 400)])
def test_disable_rejects_invalid_request_before_touching_store(rig, body, status):
    assert rig.client.post(routes.BASE + '/disable', json=body).status_code == status
    assert not rig.root.exists()
