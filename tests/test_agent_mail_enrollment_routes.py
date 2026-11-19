"""Per-call human proof, and the real pull-signup composition behind the HTTP contract.

The legacy tests use the injected ``factory`` seam. The Rig tests run the production
composition (routes + MailFactory + store + seal + Signup + Activation) against the
synthetic signed service from the signup/activation harnesses: synthetic keys, temp
dirs, no socket, no vault, no thread.
"""
import base64
import json

import pytest
from flask import Flask

from mc import agent_mail_factory as fac
from mc import state
from mc.blueprints import agent_mail_enrollment_routes as routes
from mc.blueprints import local_auth
from mc.blueprints.agent_mail_routes import create_blueprint as ingress_blueprint
from test_agent_mail_activation import ActivationHarness
from test_agent_mail_signup import MASTER, NOW, ORIGIN, OWNER

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


def test_registry_default_is_pull_composition_not_the_unavailable_legacy_adapter(tmp_path, monkeypatch):
    """The registry's real default: no injected factory, flag NOT set, pristine home."""
    mail = fac.MailFactory(tmp_path / 'mail', clock=lambda: NOW / 1000, key=lambda: MASTER)
    monkeypatch.setattr(fac, 'get', lambda: mail)
    monkeypatch.delitem(state.CONFIG, routes.FLAG, raising=False)
    app = Flask(__name__)
    app.register_blueprint(ingress_blueprint(tmp_path / 'data'))
    client = app.test_client()
    assert 'agent_mail_ingress.agent_mail_enrollment' in app.blueprints
    result = client.get(routes.BASE)
    assert result.status_code == 200 and result.json['protocol'] == 'pull', result.json
    assert result.json['signup']['state'] == result.json['pull_status']['state'] == 'not_enrolled'
    assert result.headers['Cache-Control'] == 'no-store'
    assert not list(tmp_path.iterdir())  # building the blueprint and reading status create nothing


# --- real pull composition: MailFactory + Signup + Activation over a synthetic service ---------------

ROOT_KEY_VALUE = base64.urlsafe_b64encode(b'R' * 32).rstrip(b'=').decode('ascii')
CUSTOM_ROOT = base64.urlsafe_b64encode(b'C' * 32).rstrip(b'=').decode('ascii')
HUMAN = {'passcode': 'human-fixture'}


class Rig:
    """The production composition end to end. Only the HTTPS service, the vault key, the
    clock and the supervisor thread are synthetic; the routes, MailFactory, store, seal,
    Signup and Activation are the real classes."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.h = ActivationHarness(tmp_path / 'service')
        self.config_path = tmp_path / 'config.json'
        self.supervisor_starts, self.supervisor_fail = [], False
        self.defaults = (ROOT_KEY_VALUE, ORIGIN)
        monkeypatch.setattr(state, 'CONFIG', {})
        monkeypatch.setattr(state, 'agent_sessions', {})
        monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
        monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: value == 'human-fixture')
        self.root = tmp_path / 'mail'
        self.mail = self.make_factory()
        app = Flask(__name__)
        app.register_blueprint(routes.create_blueprint(
            mail_factory=lambda: self.mail, supervisor_start=self.start_supervisor,
            release_defaults=lambda: self.defaults, config_path=self.config_path))
        self.client = app.test_client()

    def start_supervisor(self, app):
        if self.supervisor_fail:
            raise RuntimeError('boom')
        self.supervisor_starts.append(app)

    def make_factory(self, **kwargs):
        return fac.MailFactory(self.root, clock=lambda: self.h.now / 1000, key=lambda: self.h.master,
                               pull_transport=self.h.transport,
                               reviewed_service=lambda: (self.h.pin, self.h.manifest_digest), **kwargs)

    def restart(self):
        self.mail = self.make_factory()

    def saved(self):
        return json.loads(self.config_path.read_text(encoding='utf-8')) if self.config_path.exists() else {}

    def post(self, action, **body):
        return self.client.post(routes.BASE + '/' + action, json={**HUMAN, **body})

    def start(self):
        return self.post('start', owner=OWNER, consent_version='v1')

    def verify(self):
        self.h.remote_state = 'verified'
        return self.post('resume')


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return Rig(tmp_path, monkeypatch)


def test_fresh_install_status_is_not_enrolled_and_touches_nothing(rig):
    result = rig.client.get(routes.BASE)
    assert result.status_code == 200 and result.json['protocol'] == 'pull'
    assert result.json['signup']['state'] == 'not_enrolled'
    assert result.json['pull_status']['state'] == 'not_enrolled' and result.json['pull_status']['v'] == 1
    assert not rig.root.exists() and not rig.h.calls and not rig.saved()
    assert routes.FLAG not in state.CONFIG


def test_held_store_stays_held_and_start_does_not_repair_it(rig):
    rig.root.mkdir()
    (rig.root / 'agent_mail.sqlite3').write_bytes(b'')  # a database with no custody marker
    status = rig.client.get(routes.BASE).json
    assert status['signup'] == {**status['signup'], 'state': 'held', 'reason': 'marker_missing'}
    assert status['pull_status']['state'] == 'held' and status['pull_status']['reason'] == 'marker_missing'
    result = rig.start()
    assert result.status_code == 409 and result.json == {'error': 'held', 'reason': 'marker_missing'}
    assert not (rig.root / 'agent_mail.marker').exists() and not rig.h.calls
    assert routes.FLAG not in rig.saved()  # no availability flag for a store that never started


def test_human_start_bootstraps_fills_release_defaults_and_sends_one_start(rig):
    result = rig.start()
    assert result.status_code == 200 and result.json['protocol'] == 'pull', result.json
    assert result.json['state'] == 'pending' and result.json['enabled'] is False
    assert result.json['verification_state'] == 'queued' and result.json['address']
    assert [c[0] for c in rig.h.calls] == ['/v1/pull/signup/start']
    # Only public deployment values plus availability were persisted; mail stays opt-in off.
    assert rig.saved() == {fac.ROOT_KEY: ROOT_KEY_VALUE, fac.ORIGIN_KEY: ORIGIN, routes.FLAG: True}
    assert state.CONFIG == rig.saved()
    assert OWNER not in result.get_data(as_text=True) and 'Private.Owner' not in result.get_data(as_text=True)
    status = rig.client.get(routes.BASE).json
    assert status['signup']['state'] == 'pending' and status['pull_status']['state'] == 'pending'
    assert status['pull_status']['recovery']['state'] == 'not_running'
    assert rig.supervisor_starts == []


def test_start_never_overwrites_an_existing_pin(rig):
    state.CONFIG.update({fac.ROOT_KEY: CUSTOM_ROOT, fac.ORIGIN_KEY: ORIGIN})
    assert rig.start().status_code == 200
    saved = rig.saved()
    assert saved[fac.ROOT_KEY] == CUSTOM_ROOT and saved[fac.ORIGIN_KEY] == ORIGIN  # the release default lost to the pin
    assert saved[routes.FLAG] is True


@pytest.mark.parametrize('present', [fac.ROOT_KEY, fac.ORIGIN_KEY])
def test_start_refuses_a_half_set_pair_instead_of_mixing_two_deployments(rig, present):
    state.CONFIG[present] = CUSTOM_ROOT if present == fac.ROOT_KEY else ORIGIN
    refused = rig.start()
    assert refused.status_code == 409 and refused.json == {'error': 'deployment_incomplete'}
    assert not rig.root.exists() and not rig.config_path.exists() and not rig.h.calls


@pytest.mark.parametrize('defaults', [('', ''), (ROOT_KEY_VALUE, 'http://insecure.example.com'), ('bad', ORIGIN)])
def test_unpublished_or_invalid_release_defaults_refuse_before_any_side_effect(rig, defaults):
    rig.defaults = defaults
    result = rig.start()
    assert result.status_code == 409 and result.json == {'error': 'deployment_unconfigured'}
    assert not rig.root.exists() and not rig.config_path.exists() and not rig.h.calls and not state.CONFIG


@pytest.mark.parametrize('body', [{'owner': 'not-an-address', 'consent_version': 'v1'},
                                  {'owner': OWNER, 'consent_version': 'bad consent!'},
                                  {'owner': OWNER}, {'consent_version': 'v1'}, {'owner': 5, 'consent_version': 'v1'},
                                  {'owner': OWNER, 'consent_version': 'v1', 'manifest': [], 'signature': 'x'}])
def test_invalid_start_body_is_refused_before_any_side_effect(rig, body):
    result = rig.post('start', **body)
    assert result.status_code == 400 and result.json == {'error': 'invalid_request'}
    assert not rig.root.exists() and not rig.config_path.exists() and not rig.h.calls


@pytest.mark.parametrize('action', ['start', 'resume', 'enable'])
def test_pull_actions_need_the_passcode_and_no_blocking_session(rig, monkeypatch, action):
    body = {'owner': OWNER, 'consent_version': 'v1'}
    assert rig.client.post(routes.BASE + '/' + action, json=body).status_code == 403
    assert rig.client.post(routes.BASE + '/' + action, json={**body, 'passcode': 'forged'}).status_code == 403
    monkeypatch.setitem(state.agent_sessions, 'dispatched', {'status': 'running', 'trigger_type': 'dispatch'})
    blocked = rig.post(action, **body)
    assert blocked.status_code == 403 and blocked.json['error'] == 'human_required'
    assert not rig.root.exists() and not rig.config_path.exists() and not rig.h.calls


@pytest.mark.parametrize('action', ['review', 'confirm', 'verify', 'owner', 'key', 'repin', 'proof'])
def test_legacy_only_actions_are_not_served_by_the_pull_composition(rig, action):
    assert rig.post(action).status_code == 404 and not rig.h.calls


def test_locked_vault_is_reported_and_unlock_resumes_the_authorized_intent(rig):
    rig.h.master = None
    result = rig.start()
    assert result.status_code == 200 and result.json['reason'] == 'vault_locked' and not rig.h.calls
    assert rig.client.get(routes.BASE).json['signup']['reason'] == 'vault_locked'
    rig.h.master = MASTER
    assert rig.post('resume').json['state'] == 'pending'
    assert len(rig.h.calls) == 1


def test_resume_after_restart_verifies_and_never_enables(rig):
    assert rig.start().json['state'] == 'pending'
    rig.restart()
    result = rig.verify()
    assert result.json['state'] == 'verified' and result.json['enabled'] is False
    assert rig.h.calls[-1][0] == '/v1/pull/signup/status'
    assert rig.supervisor_starts == [] and 'agent_mail_outbound_enabled' not in rig.saved()


def test_resume_on_a_pristine_install_is_not_enrolled_and_creates_nothing(rig):
    result = rig.post('resume')
    assert result.status_code == 200 and result.json['state'] == 'not_enrolled'
    assert not rig.root.exists() and not rig.h.calls


def test_enable_before_verification_is_refused_and_opts_nothing_in(rig):
    rig.start()
    result = rig.post('enable')
    assert result.status_code == 200 and result.json['state'] == 'held' and result.json['enabled'] is False
    assert result.json['reason'] == 'verification_pending'
    assert rig.supervisor_starts == [] and 'agent_mail_outbound_enabled' not in rig.saved()
    assert not rig.h.activation_calls


def test_enable_activates_once_opts_in_durably_then_starts_the_supervisor(rig):
    rig.start()
    rig.verify()
    result = rig.post('enable')
    assert result.status_code == 200 and result.json['state'] == 'active' and result.json['enabled'] is True
    assert {k: rig.saved()[k] for k in routes.ACTIVATION_FLAGS} == routes.ACTIVATION_FLAGS
    assert len(rig.supervisor_starts) == 1 and len(rig.h.activation_calls) == 1
    assert rig.client.get(routes.BASE).json['signup']['state'] == 'active'


def test_flag_save_failure_after_activation_is_repaired_by_enable_without_a_second_activation(rig, monkeypatch):
    rig.start()
    rig.verify()
    real = routes.write_json_atomic
    monkeypatch.setattr(routes, 'write_json_atomic', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    lost = rig.post('enable')
    assert lost.status_code == 503 and lost.json['activation'] == 'committed'
    assert rig.supervisor_starts == [] and 'agent_mail_outbound_enabled' not in state.CONFIG
    assert len(rig.h.activation_calls) == 1
    monkeypatch.setattr(routes, 'write_json_atomic', real)
    again = rig.post('enable')
    assert again.status_code == 200 and again.json['enabled'] is True
    assert len(rig.h.activation_calls) == 1  # the committed replay, not a new operation
    assert {k: rig.saved()[k] for k in routes.ACTIVATION_FLAGS} == routes.ACTIVATION_FLAGS
    assert len(rig.supervisor_starts) == 1


def test_supervisor_failure_after_activation_is_repaired_by_enable(rig):
    rig.start()
    rig.verify()
    rig.supervisor_fail = True
    first = rig.post('enable')
    assert first.status_code == 503 and first.json['error'] == 'supervisor_unavailable'
    assert first.json['activation'] == 'committed' and rig.supervisor_starts == []
    rig.supervisor_fail = False
    second = rig.post('enable')
    assert second.status_code == 200 and second.json['enabled'] is True
    assert len(rig.supervisor_starts) == 1 and len(rig.h.activation_calls) == 1


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
