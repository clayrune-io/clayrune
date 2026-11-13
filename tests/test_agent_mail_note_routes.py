"""U3: local note route. Fake-outbound contract tests plus one run through the real Outbound."""
import json

import pytest
from flask import Flask

from test_agent_mail_outbound import out, kit, setup, env  # noqa: F401  (real-Outbound fixtures)
from mc import state
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_ownership import OwnershipHeld
from mc.agent_mail_outbound import validate_request
from mc.agent_mail_store import MailStoreError
from mc.blueprints import agent_mail_note_routes as routes

URL = '/api/project/proj1/agent/mail/note'


def payload(**changes):
    return dict(dict(session_id='s1', request_id='note1', subject='Hello', body='Friday works.'), **changes)


class Fake:
    def __init__(self, prepare=None, send=None):
        self.calls = []
        self._prepare, self._send = prepare, send

    def prepare(self, project_id, session_id, request):
        self.calls.append(('prepare', project_id, session_id, dict(request)))
        if self._prepare:
            raise self._prepare
        return {'intent_id': 'i1', 'state': 'ready', 'reason': '', 'message_id': '', 'transport_state': 'ready'}

    def send(self, intent_id):
        self.calls.append(('send', intent_id))
        if self._send:
            raise self._send
        return {'intent_id': intent_id, 'state': 'accepted', 'reason': '', 'message_id': 'm@x.test',
                'transport_state': 'accepted'}


def client_for(monkeypatch, fake, *, flag=True):
    if flag is None:
        monkeypatch.delitem(state.CONFIG, routes.FLAG, raising=False)
    else:
        monkeypatch.setitem(state.CONFIG, routes.FLAG, flag)
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(factory=lambda: fake))
    return app.test_client()


@pytest.mark.parametrize('flag', [None, False, 'true', 1])
def test_404_unless_outbound_flag_is_exactly_true(monkeypatch, flag):
    fake = Fake()
    result = client_for(monkeypatch, fake, flag=flag).post(URL, json=payload())
    assert result.status_code == 404 and result.json == {'error': 'not_found'}
    assert fake.calls == []


def test_prepares_a_note_then_sends_and_returns_metadata_only(monkeypatch):
    fake = Fake()
    result = client_for(monkeypatch, fake).post(URL, json=payload())
    assert result.status_code == 200
    assert fake.calls == [
        ('prepare', 'proj1', 's1', dict(request_id='note1', subject='Hello', body='Friday works.', kind='note')),
        ('send', 'i1')]
    assert set(result.json or {}) == {'intent_id', 'state', 'reason', 'message_id', 'transport_state'}
    assert 'Friday works.' not in result.get_data(as_text=True)
    assert result.headers['Cache-Control'] == 'no-store'
    validate_request(fake.calls[0][3])  # exactly the shape Outbound itself accepts


@pytest.mark.parametrize('extra', ['to', 'recipient', 'cc', 'bcc', 'kind', 'reply_to'])
def test_no_recipient_or_kind_field_is_accepted(monkeypatch, extra):
    fake = Fake()
    result = client_for(monkeypatch, fake).post(URL, json=payload(**{extra: 'x@example.test'}))
    assert result.status_code == 400 and result.json == {'error': 'invalid_request'}
    assert fake.calls == []


@pytest.mark.parametrize('bad', [None, [], 'text', 7,
                                 {'session_id': 's1', 'request_id': 'n', 'subject': 's'},
                                 payload(session_id=1), payload(body=None), payload(subject=['a'])])
def test_malformed_body_refused_before_outbound(monkeypatch, bad):
    fake = Fake()
    result = client_for(monkeypatch, fake).post(URL, json=bad)
    assert result.status_code == 400 and fake.calls == []


def test_non_json_body_refused(monkeypatch):
    fake = Fake()
    result = client_for(monkeypatch, fake).post(URL, data='x', content_type='text/plain')
    assert result.status_code == 400 and fake.calls == []


@pytest.mark.parametrize('error,status', [
    (EnrollmentRefused('invalid_send'), 400), (EnrollmentRefused('session_unavailable'), 404),
    (EnrollmentRefused('project_unavailable'), 404), (EnrollmentRefused('vault_locked'), 503),
    (EnrollmentRefused('outbound_full'), 503), (EnrollmentRefused('intent_conflict'), 409),
    (EnrollmentRefused('consent_required'), 409), (EnrollmentRefused('outbound_disabled'), 409),
    (OwnershipHeld('ownership_capacity'), 503), (MailStoreError('SECRET path C:/x'), 503),
    (RuntimeError('SECRET boom'), 503)])
def test_refusals_map_to_closed_codes_without_leaking_exception_text(monkeypatch, error, status):
    result = client_for(monkeypatch, Fake(prepare=error)).post(URL, json=payload())
    assert result.status_code == status
    assert 'SECRET' not in result.get_data(as_text=True)
    assert set(result.json or {}) == {'error'}


def test_send_failure_after_prepare_is_also_mapped(monkeypatch):
    fake = Fake(send=EnrollmentRefused('service_key_changed'))
    result = client_for(monkeypatch, fake).post(URL, json=payload())
    assert result.status_code == 409 and result.json == {'error': 'service_key_changed'}
    assert [c[0] for c in fake.calls] == ['prepare', 'send']


def test_factory_failure_is_503_not_a_traceback(monkeypatch):
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    def broken():
        raise MailStoreError('store missing')
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(factory=broken))
    result = app.test_client().post(URL, json=payload())
    assert result.status_code == 503 and result.json == {'error': 'store_unavailable'}


def test_module_is_not_registered_by_the_ingress_registry(tmp_path):
    from mc.blueprints.agent_mail_routes import create_blueprint
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(tmp_path))
    assert not any('agent_mail_note' in name for name in app.blueprints)


def test_through_real_outbound_sends_only_content_and_binds_the_session(out, monkeypatch):
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(factory=lambda: out.outbound))
    sid = out.session['session_id']
    result = app.test_client().post(URL, json=payload(session_id=sid))
    assert result.status_code == 200, result.json
    body = result.json or {}
    assert body['state'] == 'accepted' and body['message_id'].endswith('@example.test')
    assert len(out.recording.calls) == 1
    wire = json.loads(out.recording.calls[0][3])
    assert wire['kind'] == 'note' and wire['subject'] == 'Hello'
    assert wire['body'].endswith('Replies cannot approve protected actions.')
    assert not {'to', 'recipient', 'cc', 'bcc', 'owner', 'owner_address'} & set(wire)
    with out.store._read() as db:
        row = db.execute('SELECT project_id,session_id,state FROM mail_outbound_intents').fetchone()
    assert row is not None
    assert (row['project_id'], row['session_id'], row['state']) == ('proj1', sid, 'accepted')
    # Idempotent replay: same request_id returns the same terminal metadata, no second send.
    again = app.test_client().post(URL, json=payload(session_id=sid))
    assert again.json == result.json and len(out.recording.calls) == 1


def test_through_real_outbound_unknown_session_is_404(out, monkeypatch):
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(factory=lambda: out.outbound))
    result = app.test_client().post(URL, json=payload(session_id='no-such-session'))
    assert result.status_code == 404 and result.json == {'error': 'session_unavailable'}
    assert out.recording.calls == []
