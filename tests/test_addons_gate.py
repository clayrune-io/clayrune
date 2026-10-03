"""MC-1022: every /api/addons route that installs, adopts, declines or removes
sits behind the retyped dashboard passcode (`_require_human_passcode`, the gate
MC-1030 put on install-launch). The one open write, filing a request, only
records a card. The service layer is replaced with recorders so a refused call
is provable as "never reached", and the route table is enumerated so a new
mutating route cannot be added without this file noticing.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PASSCODE = 'addon-gate-1234'

GATED = [
    ('/api/addons/requests/r1/approve', {}),
    ('/api/addons/requests/r1/decline', {}),
    ('/api/addons/install', {'addon_id': 'ffmpeg'}),
    ('/api/addons/ffmpeg/remove', {}),
]
OPEN_WRITES = {'/api/addons/requests'}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server
    from mc.addons import service
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'auth.json')
    monkeypatch.setattr(la, '_LOCAL_AUTH_FAILS', {})
    calls = []

    def rec(name, ret):
        def f(*a, **k):
            calls.append(name)
            return ret
        return f

    monkeypatch.setattr(service, 'approve', rec('approve', {'id': 'r1'}))
    monkeypatch.setattr(service, 'decline', rec('decline', {'id': 'r1'}))
    monkeypatch.setattr(service, 'install_direct', rec('install_direct', ({'id': 'r1'}, 'created')))
    monkeypatch.setattr(service, 'remove', rec('remove', {'removed': 'ffmpeg'}))
    monkeypatch.setattr(service, 'card', lambda rec_: dict(rec_))
    server.app.config['TESTING'] = True
    return server.app.test_client(), calls, la


def test_every_mutating_addons_route_is_either_gated_or_the_one_open_write():
    import server
    mutating = {r.rule.replace('<request_id>', 'r1').replace('<addon_id>', 'ffmpeg')
                for r in server.app.url_map.iter_rules()
                if r.rule.startswith('/api/addons') and (r.methods or set()) & {'POST', 'PUT', 'PATCH', 'DELETE'}}
    assert mutating == {p for p, _ in GATED} | OPEN_WRITES


@pytest.mark.parametrize('path,body', GATED)
def test_refused_when_no_passcode_is_configured(env, path, body):
    client, calls, _la = env
    resp = client.post(path, json=dict(body, passcode='whatever'))
    assert resp.status_code == 403
    assert resp.get_json()['error'] == 'passcode_required'
    assert calls == []


@pytest.mark.parametrize('path,body', GATED)
def test_refused_without_or_with_a_wrong_passcode(env, path, body):
    client, calls, la = env
    la._local_auth_set_passcode(PASSCODE)
    for b in (body, dict(body, passcode='not-the-real-one')):
        resp = client.post(path, json=b)
        assert resp.status_code == 403
        assert resp.get_json()['error'] == 'bad_passcode'
    assert calls == []


@pytest.mark.parametrize('path,body', GATED)
def test_forged_origin_alone_does_not_pass(env, path, body):
    client, calls, la = env
    la._local_auth_set_passcode(PASSCODE)
    resp = client.post(path, json=body, headers={'Origin': 'http://localhost:5199'})
    assert resp.status_code == 403
    assert calls == []


@pytest.mark.parametrize('path,body,hit', [
    ('/api/addons/requests/r1/approve', {}, 'approve'),
    ('/api/addons/requests/r1/decline', {}, 'decline'),
    ('/api/addons/install', {'addon_id': 'ffmpeg'}, 'install_direct'),
    ('/api/addons/ffmpeg/remove', {}, 'remove'),
])
def test_the_right_passcode_reaches_the_service(env, path, body, hit):
    client, calls, la = env
    la._local_auth_set_passcode(PASSCODE)
    resp = client.post(path, json=dict(body, passcode=PASSCODE))
    assert resp.status_code in (200, 202), resp.get_json()
    assert calls == [hit]
