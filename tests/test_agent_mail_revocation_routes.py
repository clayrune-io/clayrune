"""Human-gated replay of the original pull denial through the real route."""
import pytest

from mc import state
from mc.blueprints.agent_mail_enrollment_routes import BASE
from test_agent_mail_denial_routes import OP, fence_service, rig  # noqa: F401


def test_retry_replays_due_fence_after_restart_and_never_reenables(rig, monkeypatch):
    initial = fence_service(rig, offline=True)
    rig.start()
    rig.verify()
    assert rig.post('enable').json['enabled'] is True
    assert rig.post('disable', op_id=OP).json['revocation_pending'] is True
    assert len(initial) == 1
    with rig.mail.require_pull_store()._read() as db:
        due = db.execute('SELECT next_attempt_ms FROM mail_pull_fences').fetchone()[0]

    rig.restart()
    attempts = fence_service(rig)
    monkeypatch.setitem(state.agent_sessions, 'scheduled', {'status': 'running', 'trigger_type': 'schedule'})
    waiting = rig.post('retry-revocations')
    assert waiting.status_code == 200 and waiting.json['revocation_pending'] is True
    assert attempts == []
    rig.h.now = due
    assert rig.client.post(BASE + '/retry-revocations', json={'passcode': 'forged'}).status_code == 403
    assert attempts == []
    done = rig.post('retry-revocations')
    assert done.status_code == 200 and done.headers['Cache-Control'] == 'no-store'
    assert done.json['state'] == 'disabled' and done.json['revocation_pending'] is False
    assert done.json['enabled'] is False and len(attempts) == 1
    fields = ('op_id', 'install_id', 'generation', 'flow_id', 'mode', 'fence_token')
    assert [attempts[0][k] for k in fields] == [initial[0][k] for k in fields]
    assert attempts[0]['nonce'] != initial[0]['nonce']
    with rig.mail.require_pull_store()._read() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_pull_disable_ops').fetchone()[0] == 1
        assert db.execute('SELECT state FROM mail_pull_fences').fetchone()[0] == 'acked'
        assert db.execute('SELECT enabled FROM registrations').fetchone()[0] == 0


@pytest.mark.parametrize('body,status', [({'passcode': 'forged'}, 403),
                                       ({'passcode': 'human-fixture', 'op_id': OP}, 400)])
def test_retry_rejects_invalid_request_before_touching_store(rig, body, status):
    assert rig.client.post(BASE + '/retry-revocations', json=body).status_code == status
    assert not rig.root.exists()
