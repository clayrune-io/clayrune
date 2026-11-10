"""Per-call human proof, disabled wiring, and secret-free HTTP contract."""
import pytest
from flask import Flask

from mc import state
from mc.blueprints import agent_mail_enrollment_routes as routes
from mc.blueprints import local_auth
from mc.blueprints.agent_mail_routes import create_blueprint as ingress_blueprint

ACTIONS = ['start', 'resume', 'review', 'confirm', 'verify', 'enable', 'disable',
           'retry-revocations', 'owner', 'key', 'repin']


@pytest.fixture
def web(monkeypatch):
    calls = []
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    monkeypatch.setattr(state, 'agent_sessions', {})
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: value == 'human-fixture')
    app = Flask(__name__)
    def factory():
        calls.append('factory')
        raise AssertionError('refused request reached core')
    app.register_blueprint(routes.create_blueprint(factory=factory))
    return app.test_client(), calls


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('proof', [None, 'forged', 123, ['human-fixture']])
def test_forged_origin_and_manual_session_never_replace_human_passcode(web, action, proof):
    client, calls = web
    result = client.post(routes.BASE + '/' + action, json={'passcode': proof},
                         headers={'Origin': 'http://localhost:5199', 'x-mail-signature': 'service-signed'})
    assert result.status_code == 403 and calls == []
    assert result.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('action', sorted(routes.ENABLE_ACTIONS))
def test_unattended_refused_even_with_valid_passcode(web, monkeypatch, action):
    client, calls = web
    monkeypatch.setitem(state.agent_sessions, 'dispatched', {'status': 'running', 'trigger_type': 'dispatch'})
    result = client.post(routes.BASE + '/' + action, json={'passcode': 'human-fixture'})
    assert result.status_code == 403 and calls == []


@pytest.mark.parametrize('action', ACTIONS)
def test_unconfigured_passcode_never_bootstraps_authority(web, monkeypatch, action):
    client, calls = web
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: False)
    result = client.post(routes.BASE + '/' + action, json={'passcode': 'human-fixture'})
    assert result.status_code == 403 and result.json['error'] == 'passcode_required'
    assert calls == []


def test_registry_installs_separate_blueprint_but_no_operational_factory(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(ingress_blueprint(tmp_path))
    client = app.test_client()
    assert 'agent_mail_ingress.agent_mail_enrollment' in app.blueprints
    monkeypatch.delitem(state.CONFIG, routes.FLAG, raising=False)
    assert client.get(routes.BASE).status_code == 404
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    assert client.get(routes.BASE).json == {'error': 'operational_adapter_unavailable'}
    assert client.get(routes.BASE).status_code == 503
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('value', [None, [], 'invalid', 123])
def test_malformed_mutation_body_is_refused(web, value):
    client, calls = web
    assert client.post(routes.BASE + '/enable', json=value).status_code == 400
    assert calls == []


def test_get_cannot_verify_enable_or_repin(web):
    client, calls = web
    for action in ACTIONS:
        assert client.get(routes.BASE + '/' + action).status_code == 405
    assert calls == []
