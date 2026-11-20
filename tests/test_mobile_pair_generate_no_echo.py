"""MC-1179 MB35 (the part that needs no protocol decision).

When the control plane's mobile-token response lacks `client_id` / `client_secret`
the host route answers 502 `cp_incomplete_response`. It used to attach the whole
control plane body as `cp_body`. A control plane built from master answers with a
one-time `pair_token` instead of those two fields, so that 502 carried a live
pairing credential back to the caller.

No real control plane, keystore or provider is touched: all three are stubs.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

ORIGIN = {'Origin': 'http://localhost:5199'}
PAIR_TOKEN = 'one-time-pair-token-DO-NOT-ECHO'


@pytest.fixture()
def client():
    import server
    server.app.config['TESTING'] = True
    return server.app.test_client()


def _stub_backend(monkeypatch, cp_body):
    from mc.blueprints import push_mobile
    from mc_remote import enrollment
    monkeypatch.setattr(push_mobile, '_get_remote_provider', lambda: object())
    monkeypatch.setattr(push_mobile, '_mobile_pair_load_keystore_identity',
                        lambda: ('dev_test', {}, None))
    calls = []

    def fake_create(**kw):
        calls.append(kw)
        return cp_body
    monkeypatch.setattr(enrollment, 'create_mobile_token_via_cp', fake_create)
    return calls


def test_incomplete_control_plane_response_is_not_echoed(client, monkeypatch):
    calls = _stub_backend(monkeypatch, {
        'ok': True, 'token_id': 'mt_1', 'label': 'Phone', 'hostname': 'probe.invalid',
        'pair_token': PAIR_TOKEN, 'refresh_url': 'https://api.probe.invalid/v1/session/refresh',
    })
    r = client.post('/api/mobile-pair/generate', json={'label': 'Phone'}, headers=ORIGIN)
    assert len(calls) == 1
    assert r.status_code == 502
    body = r.get_json()
    assert body['error'] == 'cp_incomplete_response'
    assert body['message']
    assert 'cp_body' not in body
    assert 'pair_token' in body['cp_fields']  # names only, for diagnosis
    assert PAIR_TOKEN not in r.get_data(as_text=True)


def test_complete_response_still_returns_the_pair_uri(client, monkeypatch):
    _stub_backend(monkeypatch, {
        'ok': True, 'token_id': 'mt_2', 'label': 'Phone', 'hostname': 'probe.invalid',
        'client_id': 'cid', 'client_secret': 'csecret',
    })
    r = client.post('/api/mobile-pair/generate', json={'label': 'Phone'}, headers=ORIGIN)
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True
    assert body['pair_uri'].startswith('clayrune://pair?')
    assert body['client_id'] == 'cid'
