"""Retry the same activation after either ordering of a transport loss."""
import json

import pytest

from mc.agent_mail_activation import PATH
from mc.blueprints.agent_mail_enrollment_routes import BASE
from test_agent_mail_enrollment_routes import rig  # noqa: F401


@pytest.mark.parametrize('service_committed', [False, True], ids=['request-lost', 'response-lost'])
def test_lost_enable_keeps_same_operation_retryable_after_refresh(rig, service_committed):
    rig.start()
    rig.verify()
    transmitted = []

    def transport(method, path, headers, raw):
        if path != PATH:
            return rig.h.transport(method, path, headers, raw)
        transmitted.append((dict(headers), raw))
        if len(transmitted) == 1:
            if service_committed:
                rig.h.transport(method, path, headers, raw)
            raise OSError('synthetic connection loss')
        return rig.h.transport(method, path, headers, raw)

    rig.mail._pull_transport = transport
    lost = rig.post('enable').json
    assert lost['state'] == 'held' and lost['reason'] == 'connection_lost' and lost['enabled'] is False
    assert len(rig.h.activation_operations) == int(service_committed)
    assert not rig.supervisor_starts
    rig.restart()
    rig.mail._pull_transport = transport
    refresh = rig.client.get(BASE).json['signup']
    assert refresh['state'] == 'enable_pending' and refresh['reason'] == ''
    assert refresh['activation_attempts'] == 1 and refresh['enabled'] is False
    assert len(transmitted) == 1

    assert rig.client.post(BASE + '/enable', json={'passcode': 'forged'}).status_code == 403
    assert len(transmitted) == 1
    rig.h.now += 1000
    active = rig.post('enable').json
    assert active['state'] == 'active' and active['enabled'] is True and active['activation_attempts'] == 2
    first, second = transmitted
    assert first[1] == second[1]
    assert first[0]['x-mail-nonce'] != second[0]['x-mail-nonce']
    (operation,) = rig.h.activation_operations
    assert json.loads(second[1])['op_id'] == operation
    assert len(rig.h.activation_calls) == 1 + int(service_committed)
    assert len(rig.supervisor_starts) == 1
