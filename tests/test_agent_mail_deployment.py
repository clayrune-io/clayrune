"""Human trust-anchor writes, exact manifest formats and durable factory readback; no network."""
import base64
import json

import pytest
from flask import Flask

from mc import state
from mc.agent_mail_factory import INSTALL_KEY, ORIGIN_KEY, ROOT_KEY, MailFactory
from mc.blueprints import agent_mail_deployment_routes as routes
from mc.blueprints import local_auth
from mc.blueprints.agent_mail_routes import create_blueprint as ingress_blueprint

ROOT = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b'=').decode()
ORIGIN = 'https://mail.example.com'
GOOD = {ROOT_KEY: ROOT, ORIGIN_KEY: ORIGIN, 'passcode': 'human-fixture'}


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setattr(state, 'CONFIG', {INSTALL_KEY: 'fixture-install', 'unrelated': 'preserved'})
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: value == 'human-fixture')
    path = tmp_path / 'config.json'
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(config_path=path))
    return app.test_client(), path


def test_get_before_any_mail_flag_is_enabled_is_public_and_read_only(web):
    client, path = web
    result = client.get(routes.ROUTE)
    assert result.status_code == 200
    assert result.json == {ROOT_KEY: '', ORIGIN_KEY: '', 'set': {ROOT_KEY: False, ORIGIN_KEY: False}}
    assert result.headers['Cache-Control'] == 'no-store'
    assert not path.exists()


@pytest.mark.parametrize('proof', [None, '', 'forged', 42, ['human-fixture']])
def test_agent_with_forged_browser_origin_has_no_human_proof(web, proof):
    client, path = web
    result = client.put(routes.ROUTE, json={**GOOD, 'passcode': proof},
                        headers={'Origin': 'http://localhost:5199'})
    assert result.status_code == 403
    assert not path.exists() and ROOT_KEY not in state.CONFIG


def test_unattended_caller_refused_even_with_passcode(web, monkeypatch):
    client, path = web
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    assert client.put(routes.ROUTE, json=GOOD).status_code == 403
    assert not path.exists()


def test_no_passcode_configured_refuses_bootstrapping_trust(web, monkeypatch):
    client, path = web
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: False)
    result = client.put(routes.ROUTE, json=GOOD)
    assert (result.status_code, result.json['error']) == (403, 'passcode_required')
    assert not path.exists()


def test_shared_passcode_guess_limit_is_preserved(web, monkeypatch):
    client, path = web
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: None)
    assert client.put(routes.ROUTE, json=GOOD).status_code == 429
    assert not path.exists()


@pytest.mark.parametrize('value', [None, '', 1, [], 'a' * 42, 'a' * 44, ROOT + '=',
                                   ROOT + '\n', ' ' + ROOT, 'a+' * 21 + 'a', 'A' * 42 + 'B'])
def test_invalid_root_key_rejected_without_changing_either_value(web, value):
    client, path = web
    result = client.put(routes.ROUTE, json={**GOOD, ROOT_KEY: value})
    assert (result.status_code, result.json['error']) == (400, 'invalid_root_public_key')
    assert ROOT_KEY not in state.CONFIG and ORIGIN_KEY not in state.CONFIG and not path.exists()


@pytest.mark.parametrize('value', [None, '', [], 1, 'http://mail.example.com',
    ORIGIN + '/', ORIGIN + '/path', ORIGIN + ':443', ORIGIN + '?x=1', ORIGIN + '#x',
    'https://user@mail.example.com', 'https://localhost', 'https://127.0.0.1',
    'https://MAIL.example.com', 'https://-mail.example.com', ORIGIN + '\n',
    'https://' + 'a' * 64 + '.example.com'])
def test_invalid_origin_rejected_without_changing_either_value(web, value):
    client, path = web
    result = client.put(routes.ROUTE, json={**GOOD, ORIGIN_KEY: value})
    assert (result.status_code, result.json['error']) == (400, 'invalid_service_origin')
    assert ROOT_KEY not in state.CONFIG and ORIGIN_KEY not in state.CONFIG and not path.exists()


@pytest.mark.parametrize('body', [None, [], 'bad', {}, {ROOT_KEY: ROOT}, {**GOOD, 'agent_mail_enrollment_enabled': True}])
def test_malformed_or_extra_fields_cannot_enable_mail(web, body):
    client, path = web
    result = client.put(routes.ROUTE, json=body)
    assert result.status_code in (400, 403)
    assert not path.exists()


def test_persisted_values_are_read_back_and_consumed_by_existing_factory(web, tmp_path):
    client, path = web
    live = MailFactory(root=tmp_path / 'mail')
    result = client.put(routes.ROUTE, json=GOOD)
    assert result.status_code == 200 and result.json['ok'] is True
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved == {INSTALL_KEY: 'fixture-install', 'unrelated': 'preserved', ROOT_KEY: ROOT, ORIGIN_KEY: ORIGIN}
    assert state.CONFIG == saved
    assert client.get(routes.ROUTE).json == {ROOT_KEY: ROOT, ORIGIN_KEY: ORIGIN,
                                           'set': {ROOT_KEY: True, ORIGIN_KEY: True}}
    factory = MailFactory(root=tmp_path / 'mail', config=saved)
    assert factory._deployment() == ('fixture-install', ROOT, ORIGIN)
    # A pre-existing factory also observes the shared config without a server restart.
    assert live._deployment() == ('fixture-install', ROOT, ORIGIN)
    assert not (tmp_path / 'mail').exists()


def test_failed_write_preserves_previous_disk_and_memory(web, monkeypatch):
    client, path = web
    assert client.put(routes.ROUTE, json=GOOD).status_code == 200
    before = path.read_bytes(), dict(state.CONFIG)
    def fail(*args, **kwargs):
        raise OSError('fixture disk failure')
    monkeypatch.setattr(routes, 'write_json_atomic', fail)
    result = client.put(routes.ROUTE, json={**GOOD, ORIGIN_KEY: 'https://new.example.com'})
    assert (result.status_code, result.json['error']) == (503, 'storage_unavailable')
    assert (path.read_bytes(), state.CONFIG) == before


def test_mail_blueprint_registers_deployment_without_flags_or_factory(tmp_path):
    app = Flask(__name__)
    app.register_blueprint(ingress_blueprint(tmp_path))
    assert 'agent_mail_ingress.agent_mail_deployment' in app.blueprints
    assert app.test_client().get(routes.ROUTE).status_code == 200


def test_generic_settings_allowlist_cannot_bypass_trust_validation():
    from mc.blueprints.settings_routes import _CONFIG_EDITABLE_KEYS
    assert ROOT_KEY not in _CONFIG_EDITABLE_KEYS and ORIGIN_KEY not in _CONFIG_EDITABLE_KEYS


def test_registered_blueprint_uses_the_wired_config_path(web, monkeypatch):
    from mc.blueprints import settings_routes
    _, path = web
    monkeypatch.setattr(settings_routes, 'CONFIG_PATH', path)
    app = Flask(__name__)
    app.register_blueprint(ingress_blueprint(path.parent))
    result = app.test_client().put(routes.ROUTE, json=GOOD)
    assert result.status_code == 200
    assert json.loads(path.read_text(encoding='utf-8'))[ROOT_KEY] == ROOT
