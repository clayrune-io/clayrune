"""Ron, 2026-10-09: the "Allow once" gate also offers "Allow 10 min".

One passcode used to cover exactly one blocked action, so a failed action
meant typing it again. `mode: window` opens a pass that every fence-blocked
action in the session goes through until it expires (mc/attend_once_pass.py).
Granting stays passcode-gated; revoking ("Re-arm now") needs none because it
only closes the gate. Everything goes through the Flask test client, never the
live server.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mc import attend_once_pass as pass_mod

PASSCODE = 'window-1234'
CSID = 'csid-window'
PID = 'proj-a'
SID = 'sid-w'


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprints)
    from mc import state as mc_state
    from mc.blueprints import local_auth as la
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    mc_state.agent_sessions[SID] = {'project_id': PID, 'claude_session_id': CSID,
                                    'trigger_type': 'dispatch'}
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _session():
    from mc import state as mc_state
    return mc_state.agent_sessions[SID]


def _grant(client, mode=None, passcode=PASSCODE, sid=SID):
    from mc.blueprints import local_auth as la
    if not la._local_auth_is_configured():
        la._local_auth_set_passcode(PASSCODE)
    body = {}
    if passcode is not None:
        body['passcode'] = passcode
    if mode is not None:
        body['mode'] = mode
    return client.post(f'/api/project/{PID}/agent/{sid}/attend-once', json=body)


def _consume(client, **extra):
    return client.post('/api/session/attend-once/consume',
                       json={'claude_session_id': CSID, **extra})


def _revoke(client, sid=SID, pid=PID):
    return client.post(f'/api/project/{pid}/agent/{sid}/attend-once/revoke', json={})


# ── grant ─────────────────────────────────────────────────────────────────────

def test_default_grant_is_once_and_reports_its_mode(client):
    body = _grant(client).get_json()
    assert body['status'] == 'granted' and body['mode'] == 'once'
    assert _session()['_attend_once_pass']['mode'] == 'once'


def test_window_grant_records_mode_and_ten_minute_expiry(client):
    before = datetime.now(timezone.utc)
    body = _grant(client, 'window').get_json()
    assert body['status'] == 'granted' and body['mode'] == 'window'
    expires = datetime.fromisoformat(body['expires_at'])
    assert timedelta(seconds=595) < expires - before <= timedelta(seconds=601)
    assert _session()['_attend_once_pass']['mode'] == 'window'


def test_unknown_mode_is_400_and_grants_nothing(client):
    from mc.blueprints import local_auth as la
    la._local_auth_set_passcode(PASSCODE)
    for bad in ('forever', '', None, 5, 'WINDOW'):
        resp = client.post(f'/api/project/{PID}/agent/{SID}/attend-once',
                           json={'passcode': PASSCODE, 'mode': bad})
        assert resp.status_code == 400, bad
        assert '_attend_once_pass' not in _session()


def test_window_grant_still_needs_the_passcode(client):
    assert _grant(client, 'window', passcode=None).status_code in (401, 403)
    assert _grant(client, 'window', passcode='not-it').status_code in (401, 403)
    assert '_attend_once_pass' not in _session()


def test_window_grant_keeps_the_eligibility_gate(client):
    _session()['trigger_type'] = 'manual'
    assert _grant(client, 'window').status_code == 409
    assert '_attend_once_pass' not in _session()


def test_window_grant_keeps_the_passcode_changed_gate(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la
    la._local_auth_set_passcode(PASSCODE)
    _session()['started_at'] = '2020-01-01T00:00:00Z'
    monkeypatch.setattr(ar, '_local_auth_passcode_set_at',
                        lambda: datetime.now(timezone.utc).timestamp())
    assert _grant(client, 'window').status_code == 409
    assert '_attend_once_pass' not in _session()


# ── consume ───────────────────────────────────────────────────────────────────

def test_window_is_not_spent_by_consume(client):
    _grant(client, 'window')
    expiry = _session()['_attend_once_pass']['expires_at']
    for i in range(3):
        body = _consume(client, tool_use_id=f'tu-{i}', action_digest=f'd-{i}').get_json()
        assert body['consumed'] is True and body['mode'] == 'window'
    assert _session()['_attend_once_pass']['expires_at'] == expiry


def test_window_covers_a_retry_of_the_same_call_and_a_repeat_without_identity(client):
    _grant(client, 'window')
    assert _consume(client, tool_use_id='tu-1', action_digest='d').get_json()['consumed']
    assert _consume(client, tool_use_id='tu-1', action_digest='d').get_json()['consumed']
    assert _consume(client).get_json()['consumed']


def test_once_is_still_spent_by_the_first_consume(client):
    _grant(client)
    assert _consume(client).get_json()['consumed'] is True
    assert '_attend_once_pass' not in _session()
    assert _consume(client).get_json()['consumed'] is False


def test_expired_window_is_not_spendable_and_is_evicted(client):
    _grant(client, 'window')
    _session()['_attend_once_pass']['expires_at'] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert _consume(client).get_json()['consumed'] is False
    assert '_attend_once_pass' not in _session()


def test_window_still_refuses_a_pass_bound_to_another_session(client):
    from mc import state as mc_state
    mc_state.agent_sessions[SID]['_attend_once_pass'] = {
        'expires_at': '2099-01-01T00:00:00+00:00', 'mc_session_id': 'sid-other', 'mode': 'window'}
    resp = _consume(client)
    assert resp.status_code == 409 and resp.get_json()['consumed'] is False


def test_consume_cannot_create_a_window(client):
    assert _consume(client).get_json()['consumed'] is False
    assert '_attend_once_pass' not in _session()


# ── no extend / no stack / upgrade ────────────────────────────────────────────

def test_second_window_grant_does_not_extend(client):
    first = _grant(client, 'window').get_json()
    second = _grant(client, 'window').get_json()
    assert second['status'] == 'already_open' and second['mode'] == 'window'
    assert second['expires_at'] == first['expires_at']


def test_window_then_once_keeps_the_window_and_its_expiry(client):
    first = _grant(client, 'window').get_json()
    second = _grant(client, 'once').get_json()
    assert second['status'] == 'already_open' and second['mode'] == 'window'
    assert second['expires_at'] == first['expires_at']
    assert _session()['_attend_once_pass']['mode'] == 'window'


def test_open_once_pass_upgrades_to_a_window_with_a_fresh_expiry(client):
    _grant(client)
    older = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    _session()['_attend_once_pass']['expires_at'] = older
    body = _grant(client, 'window').get_json()
    assert body['status'] == 'upgraded' and body['mode'] == 'window'
    assert body['expires_at'] > older
    assert _session()['_attend_once_pass']['mode'] == 'window'
    assert _consume(client).get_json()['consumed'] is True
    assert _consume(client).get_json()['consumed'] is True


def test_once_then_once_still_does_not_stack(client):
    first = _grant(client).get_json()
    second = _grant(client).get_json()
    assert second['status'] == 'already_open' and second['expires_at'] == first['expires_at']


def test_expired_window_can_be_granted_again(client):
    _grant(client, 'window')
    _session()['_attend_once_pass']['expires_at'] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert _grant(client, 'window').get_json()['status'] == 'granted'


# ── revoke ────────────────────────────────────────────────────────────────────

def test_revoke_closes_a_window_without_a_passcode(client):
    _grant(client, 'window')
    body = _revoke(client).get_json()
    assert body['status'] == 'revoked' and body['mode'] == 'window'
    assert '_attend_once_pass' not in _session()
    assert _consume(client).get_json()['consumed'] is False


def test_revoke_closes_a_once_pass_too(client):
    _grant(client)
    assert _revoke(client).get_json()['status'] == 'revoked'
    assert _consume(client).get_json()['consumed'] is False


def test_revoke_with_nothing_open_is_a_noop(client):
    body = _revoke(client).get_json()
    assert body['ok'] is True and body['status'] == 'none_open'


def test_revoke_unknown_session_and_wrong_project_404(client):
    assert _revoke(client, sid='nope').status_code == 404
    assert _revoke(client, pid='proj-b').status_code == 404


def test_grant_after_revoke_needs_a_new_passcode_grant(client):
    _grant(client, 'window')
    _revoke(client)
    assert _consume(client).get_json()['consumed'] is False
    assert _grant(client, 'window').get_json()['status'] == 'granted'


# ── status payload ────────────────────────────────────────────────────────────

def test_agent_status_carries_the_pass_mode(client):
    from mc import state as mc_state
    mc_state.agent_sessions[SID].update({
        'session_id': SID, 'status': 'idle', 'task': '', 'log_lines': [],
        'started_at': '', 'usage': {}, 'cost_usd': 0, 'num_turns': 0})
    _grant(client, 'window')
    rows = client.get(f'/api/project/{PID}/agent/status').get_json()['sessions']
    row = next(s for s in rows if s['session_id'] == SID)
    assert row['attend_once_pass']['open'] is True
    assert row['attend_once_pass']['mode'] == 'window'


# ── module unit ───────────────────────────────────────────────────────────────

def test_parse_mode():
    assert pass_mod.parse_mode({}) == 'once'
    assert pass_mod.parse_mode({'mode': 'once'}) == 'once'
    assert pass_mod.parse_mode({'mode': 'window'}) == 'window'
    for bad in ('x', None, '', 3, ['window']):
        assert pass_mod.parse_mode({'mode': bad}) is None
    assert pass_mod.parse_mode(None) == 'once'


def test_record_without_a_mode_reads_as_once():
    s = {'_attend_once_pass': {'expires_at': '2099-01-01T00:00:00+00:00', 'mc_session_id': 'x'}}
    assert pass_mod.view(s)['mode'] == 'once'
    assert pass_mod.spend(s) == 'once'
    assert '_attend_once_pass' not in s


def test_view_never_mutates_an_expired_record():
    s = {'_attend_once_pass': {'expires_at': '2000-01-01T00:00:00+00:00', 'mode': 'window'}}
    assert pass_mod.view(s) is None
    assert '_attend_once_pass' in s
    pass_mod.evict(s)
    assert '_attend_once_pass' not in s


def test_corrupt_expiry_reads_closed():
    s = {'_attend_once_pass': {'expires_at': 'garbage', 'mode': 'window'}}
    assert pass_mod.view(s) is None
    pass_mod.evict(s)
    assert '_attend_once_pass' not in s
