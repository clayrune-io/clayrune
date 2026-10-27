"""MC-1117: dropping a held X sign-in must not revoke the grant a SAVED sign-in shares.

Live incident (acct-0362a76b, 2026-10-26): a re-sign-in from Permissions was held beside the
account's working sign-in, cancelled, and the revoke of the held token took the saved refresh
token with it (X answered 400, the account went `needs_signin`). Pinned here:

  * cancel, back-out, expiry and a cancel while the code is exchanged FORGET the held tokens and
    make no revoke call when the account already has a saved sign-in from the same app, and log
    '[desk_oauth] held x sign-in dropped without revoke: account has a saved sign-in';
  * a brand-new account (nothing saved) and a held sign-in from a DIFFERENT app still revoke;
  * an entry from before the app hash existed counts as saved; a vault that cannot be asked
    answers "do not revoke";
  * the saved sign-in's record is byte-for-byte what it was.

Same real temp vault / Desk store / scripted vendor as the 03b attach suite. No real network,
no real sign-in, no named browser profile.
"""
from __future__ import annotations

import time

import pytest

from mc import desk_account_refs, desk_oauth, desk_oauth_hold as hold, desk_oauth_saved_guard as guard, secrets_store
from tests.test_desk_connect_signin_reuse import second_sign_in, signed_in
from tests.test_desk_oauth import ACCESS_1, REFRESH_1
from tests.test_desk_oauth_hold import (PASSCODE, X_REVOKE, X_TOKEN, _sign_in, _start_x,  # noqa: F401
                                      api, profiles, env, _clean_flows)

SKIPPED = '[desk_oauth] held x sign-in dropped without revoke: account has a saved sign-in'


@pytest.fixture()
def logs(monkeypatch):
    lines: list[str] = []
    for mod in (hold, guard):
        monkeypatch.setattr(mod, '_log', lambda msg, **k: lines.append(msg))
    return lines


def saved_blob(aid):
    name = desk_oauth.vault_name('x', desk_account_refs.require_oauth_arg(aid))
    return secrets_store.get_secret_value(name, consumer='test', internal=True)


def held_beside_saved(client, p):
    aid, _ = signed_in(client, p)
    before = saved_blob(aid)
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200 and hold.alive(out['flow_id'])
    return aid, before, out


def cancel(client, out):
    return client.post(f'/api/desk/connect/flows/{out["flow_id"]}/cancel', json={'claim': out['claim']})


def test_cancelling_a_re_sign_in_makes_no_revoke_call_and_leaves_the_saved_sign_in(api, logs):
    client, p = api
    aid, before, out = held_beside_saved(client, p)
    assert cancel(client, out).get_json() == {'ok': True}
    assert hold.alive(out['flow_id']) == 0
    assert p.to(X_REVOKE) == []
    assert SKIPPED in logs and not any('revoked=' in m for m in logs)
    assert saved_blob(aid) == before
    assert desk_oauth.status('x', desk_account_refs.require_oauth_arg(aid))['state'] == 'connected'


def test_expiry_of_a_held_re_sign_in_makes_no_revoke_call(api, logs):
    client, p = api
    aid, before, out = held_beside_saved(client, p)
    hold._expire(out['flow_id'])             # what the TTL timer runs
    assert hold.alive(out['flow_id']) == 0
    assert p.to(X_REVOKE) == [] and SKIPPED in logs
    assert saved_blob(aid) == before


def test_a_cancel_while_the_code_is_exchanged_makes_no_revoke_call(api, logs):
    client, p = api
    aid, _ = signed_in(client, p)
    before = saved_blob(aid)
    out = second_sign_in(client, p, aid)
    real = desk_oauth._token_response

    def cancel_then_answer(*a, **k):
        assert cancel(client, out).get_json() == {'ok': True}
        return real(*a, **k)
    desk_oauth._token_response = cancel_then_answer
    try:
        _sign_in(out)
    finally:
        desk_oauth._token_response = real
    assert hold.alive(out['flow_id']) == 0
    assert p.to(X_REVOKE) == [] and SKIPPED in logs
    assert saved_blob(aid) == before


def test_a_brand_new_account_still_revokes_on_cancel(api, logs):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)
    assert cancel(client, out).get_json() == {'ok': True}
    assert [c['form']['token_type_hint'] for c in p.to(X_REVOKE)] == ['refresh_token', 'access_token']
    assert '[desk_oauth] held x sign-in dropped (revoked=True)' in logs and SKIPPED not in logs


def test_a_held_sign_in_from_another_app_still_revokes(api, logs):
    client, p = api
    aid, _ = signed_in(client, p)
    before = saved_blob(aid)
    p.on('POST', X_TOKEN, (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1, 'expires_in': 7200}))
    for _ in range(40):         # the first flow's return-address listener closes a moment after its Save
        r = client.post('/api/desk/connect/x/start-held',
                        json={'passcode': PASSCODE, 'account_id': aid, 'hold': {'client_id': 'another-app-9999'}})
        out = r.get_json()
        if out.get('code') != 'port_busy':
            break
        time.sleep(0.25)
    assert r.status_code == 201, out
    _sign_in(out)
    assert cancel(client, out).get_json() == {'ok': True}
    assert len(p.to(X_REVOKE)) == 2 and SKIPPED not in logs
    assert saved_blob(aid) == before


# -- the decision itself ------------------------------------------------------------------

def meta(monkeypatch, value):
    monkeypatch.setattr(desk_oauth, '_meta', lambda service, account_id=None: value)


def test_skip_reason_by_entry(monkeypatch):
    import hashlib
    rec = {'client_id': 'app-1'}
    meta(monkeypatch, None)
    assert guard.skip_reason('x', 'acct-1', rec) is None                                   # nothing saved
    meta(monkeypatch, {'hint': 'state=ok exp=1 refresh=yes app_id_sha256=' + hashlib.sha256(b'app-1').hexdigest()})
    assert guard.skip_reason('x', 'acct-1', rec) == guard.SAVED                            # same app
    assert guard.skip_reason('x', 'acct-1', {'client_id': 'app-2'}) is None                # another app
    meta(monkeypatch, {'hint': 'state=ok exp=1 refresh=yes'})
    assert guard.skip_reason('x', 'acct-1', rec) == guard.SAVED                            # pre-hash entry: it exists
    assert guard.skip_reason('x', 'acct-1', None) == guard.SAVED
    assert guard.skip_reason('higgsfield', None, rec) is None                              # per-sign-in client: not applicable


def test_a_vault_that_cannot_answer_means_do_not_revoke(monkeypatch):
    def boom(*a, **k):
        raise secrets_store.SecretsError('locked')
    monkeypatch.setattr(desk_oauth, '_meta', boom)
    monkeypatch.setattr(guard, '_log', lambda *a, **k: None)
    assert guard.skip_reason('x', 'acct-1', {'client_id': 'app-1'}) == guard.UNKNOWN
