"""Held agent-mail events: metadata-only list, human-only release/reject.

Pins the gate (an agent is refused, a passcode holder is allowed), that no mail
text leaves the list, and that rejecting/releasing a hold is what unblocks the
events queued behind it. Synthetic store only: no network, no mail, no provider.
"""
import json

import pytest
from flask import Flask

from mc import state
from mc.agent_mail_store import BLOCKED, LOCAL_RECEIVED, READY, REJECTED, MailStore
from mc.blueprints import agent_mail_holds_routes as routes
from mc.blueprints import local_auth
from mc.blueprints.agent_mail_routes import create_blueprint as ingress_blueprint

REG, TOKEN, PASS = 'reg-1', 'tok-AAAA-secret-thread-token', 'human-fixture'
SECRET = 'PRIVATE-REPLY-TEXT-do-not-show'


@pytest.fixture
def web(tmp_path, monkeypatch):
    store = MailStore(tmp_path / 'agent_mail.sqlite3')
    store.register(REG)
    store.bind_thread(REG, TOKEN, 'proj', 'sess-1')
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: value == PASS)
    for sid in list(state.agent_sessions):
        monkeypatch.delitem(state.agent_sessions, sid)
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(store_factory=lambda: store))
    return app.test_client(), store


def put(store, n, body):
    store.insert_event(event_id=f'ev{n}', registration_id=REG, thread_token=TOKEN,
                       message_id=f'<m{n}@x>', body=body)


def hold_first_and_ready_second(store):
    """ev1 held (conversation_identity_changed), ev2 behind it and ready."""
    put(store, 1, SECRET)
    put(store, 2, 'second reply')
    c = store.claim_for_laundering()
    store.fail(c['event_id'], c['fence_token'], 'conversation_identity_changed', retryable=False)
    c = store.claim_for_laundering()
    assert store.complete_laundering(c['event_id'], c['fence_token'], {'reply_text': 'second reply'}) == READY
    assert store.get_event('ev1')['state'] == BLOCKED


def release(client, event_id='ev1', **kw):
    return client.post(f'{routes.BASE}/{event_id}/release', json={'passcode': PASS}, **kw)


def test_list_is_metadata_only_and_counts_what_is_queued_behind(web):
    client, store = web
    hold_first_and_ready_second(store)
    result = client.get(routes.BASE)
    assert result.status_code == 200 and result.headers['Cache-Control'] == 'no-store'
    (hold,) = result.json['holds']
    assert hold['event_id'] == 'ev1' and hold['reason'] == 'conversation_identity_changed'
    assert hold['queued_behind'] == 1 and hold['project_id'] == 'proj'
    assert set(hold) == set(routes.FIELDS) | {'queued_behind'}
    raw = json.dumps(result.json)
    assert SECRET not in raw and 'second reply' not in raw and TOKEN not in raw


def test_list_is_empty_when_nothing_is_held(web):
    client, store = web
    put(store, 1, 'fine')
    assert client.get(routes.BASE).json == {'holds': []}


@pytest.mark.parametrize('action', ['release', 'reject'])
def test_unattended_agent_is_refused_even_with_the_passcode(web, monkeypatch, action):
    client, store = web
    hold_first_and_ready_second(store)
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    result = client.post(f'{routes.BASE}/ev1/{action}', json={'passcode': PASS})
    assert (result.status_code, result.json['error']) == (403, 'human_required')
    assert store.get_event('ev1')['state'] == BLOCKED


@pytest.mark.parametrize('action', ['release', 'reject'])
def test_a_running_dispatched_session_is_refused_by_the_real_check(web, monkeypatch, action):
    client, store = web
    hold_first_and_ready_second(store)
    monkeypatch.setitem(state.agent_sessions, 's1', {'status': 'running', 'trigger_type': 'dispatch'})
    result = client.post(f'{routes.BASE}/ev1/{action}', json={'passcode': PASS})
    assert result.status_code == 403 and result.json['error'] == 'human_required'
    assert store.get_event('ev1')['state'] == BLOCKED


@pytest.mark.parametrize('action', ['release', 'reject'])
@pytest.mark.parametrize('proof', [None, '', 'forged', 42, ['human-fixture']])
def test_forged_browser_origin_is_not_a_human_proof(web, action, proof):
    client, store = web
    hold_first_and_ready_second(store)
    result = client.post(f'{routes.BASE}/ev1/{action}', json={'passcode': proof},
                         headers={'Origin': 'http://localhost:5199'})
    assert result.status_code == 403
    assert store.get_event('ev1')['state'] == BLOCKED


def test_no_passcode_configured_refuses(web, monkeypatch):
    client, store = web
    hold_first_and_ready_second(store)
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: False)
    result = release(client)
    assert (result.status_code, result.json['error']) == (403, 'passcode_required')
    assert store.get_event('ev1')['state'] == BLOCKED


def test_non_object_body_is_a_400(web):
    client, store = web
    hold_first_and_ready_second(store)
    assert client.post(f'{routes.BASE}/ev1/release', data='x', content_type='text/plain').status_code == 400


def test_human_release_returns_the_event_to_the_pipeline_and_never_auto_runs_it(web):
    client, store = web
    hold_first_and_ready_second(store)
    result = release(client)
    assert result.status_code == 200 and result.json == {'ok': True, 'state': LOCAL_RECEIVED}
    assert store.get_event('ev1')['state'] == LOCAL_RECEIVED
    assert client.get(routes.BASE).json == {'holds': []}


def test_human_reject_drops_the_event_purges_its_content_and_unblocks_the_chat(web):
    client, store = web
    hold_first_and_ready_second(store)
    assert store.claim_for_submission() is None            # head-of-line: ev1 holds ev2
    result = client.post(f'{routes.BASE}/ev1/reject', json={'passcode': PASS})
    assert result.status_code == 200 and result.json == {'ok': True, 'state': 'rejected'}
    ev = store.get_event('ev1', include_content=True)
    assert ev['state'] == REJECTED and ev['reason'] == routes.REJECT_REASON and ev['body'] is None
    assert store.claim_for_submission()['event_id'] == 'ev2'


def test_only_a_held_event_is_reviewable(web):
    client, store = web
    hold_first_and_ready_second(store)
    result = client.post(f'{routes.BASE}/ev2/reject', json={'passcode': PASS})
    assert (result.status_code, result.json) == (409, {'error': 'not_held', 'state': READY})
    assert store.get_event('ev2')['state'] == READY
    assert release(client, 'nope').status_code == 404
    assert client.post(f'{routes.BASE}/ev1/explode', json={'passcode': PASS}).status_code == 404


def test_release_of_a_hold_whose_content_was_not_kept_is_refused_with_a_reason(web):
    client, store = web
    put(store, 1, 'x' * (store.config.max_body_bytes + 1))
    assert store.get_event('ev1')['state'] == BLOCKED       # oversize_body: content never stored
    result = release(client)
    assert (result.status_code, result.json['error']) == (409, 'nothing_to_release')
    assert client.post(f'{routes.BASE}/ev1/reject', json={'passcode': PASS}).status_code == 200


def test_a_held_store_is_a_503_not_a_crash(monkeypatch):
    from mc.agent_mail_factory import StoreHeld

    def held():
        raise StoreHeld('not_enrolled')
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda _value: True)
    for sid in list(state.agent_sessions):
        monkeypatch.delitem(state.agent_sessions, sid)
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(store_factory=held))
    client = app.test_client()
    assert client.get(routes.BASE).status_code == 503
    assert client.post(f'{routes.BASE}/ev1/release', json={'passcode': PASS}).status_code == 503


def test_routes_are_wired_through_the_agent_mail_blueprint(tmp_path):
    app = Flask(__name__)
    app.register_blueprint(ingress_blueprint(tmp_path))
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert routes.BASE in rules and routes.BASE + '/<event_id>/<action>' in rules
