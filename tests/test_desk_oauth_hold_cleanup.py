"""What a held sign-in leaves behind when it is dropped, and how the Save takes it
(Wren's audit of 0ba3aa19, 2026-10-05). Companion to `test_desk_oauth_hold.py`; same fakes.

  * the named browser profile a held flow's pane created is removed on cancel, expiry, a failed
    sign-in, a replacement and an eviction, and ONLY when the flow created it (one that was
    already on disk is never touched); a Save that lands keeps it;
  * removing it closes the pane's Chromium first, gracefully, and only then deletes the directory;
  * the Save TAKES the held sign-in in one step: an expiry mid-write cannot revoke it, an already
    expired one is refused without a network call on the committing thread;
  * a cancel that arrives while the code is being exchanged leaves nothing held (and revokes what
    the vendor just minted); eviction drops the hold at once;
  * a held sign-in minted for one X app is refused for a Save that stores another;
  * a locked vault is refused before any listener, discovery or sign-in page opens.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc import desk_oauth as oauth  # noqa: E402
from mc import desk_oauth_hold as hold  # noqa: E402
from mc import desk_oauth_profile as profile_mod  # noqa: E402
from mc import secrets_store  # noqa: E402
from tests.test_desk_connect import PASSCODE, env  # noqa: E402,F401
from tests.test_desk_oauth import HF_REVOKE, HF_TOKEN, _hit, _state_of  # noqa: E402,F401
from tests.test_desk_oauth import _clean_flows  # noqa: E402,F401
from tests.test_desk_oauth_hold import (CLIENT_ID, HF_URL, X_FIELDS, X_REVOKE, X_URL, _accounts,  # noqa: E402,F401
                                        _held, _save, _sign_in, _start_hf, _start_x, _vault_names, api, profiles)

HF_PROFILE = 'desk-higgsfield'


def _wait(cond, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.03)
    return cond()


def _cancel(client, out, claim=None):
    return client.post(f'/api/desk/connect/flows/{out["flow_id"]}/cancel', json={'claim': claim or out['claim']})


# -- 1. the profile the flow created ---------------------------------------------------------

def test_cancel_removes_the_profile_the_flow_created(api, profiles):
    client, p = api
    out = _start_hf(client, p)
    profiles.have.add(out['profile'])                  # the pane the page opened wrote it
    _sign_in(out)
    assert _cancel(client, out).get_json() == {'ok': True}
    assert profiles.forgotten == [HF_PROFILE] and HF_PROFILE not in profiles.have


def test_cancel_before_the_vendor_answers_removes_it_too(api, profiles):
    client, p = api
    out = _start_x(client, p)
    profiles.have.add(out['profile'])
    assert _cancel(client, out).get_json() == {'ok': True}
    assert profiles.forgotten == [out['profile']]


def test_a_profile_that_was_already_there_is_never_removed(api, profiles, monkeypatch):
    client, p = api
    profiles.have.add(HF_PROFILE)                      # an earlier sign-in's saved login
    monkeypatch.setattr(oauth, 'HOLD_TTL_S', 0.2)
    out = _start_hf(client, p)
    _sign_in(out)
    assert _cancel(client, out).get_json() == {'ok': True}
    assert profiles.forgotten == [] and HF_PROFILE in profiles.have
    again = _start_hf(client, p)                       # and an expiry leaves it alone as well
    _sign_in(again)
    assert _wait(lambda: not hold.alive(again['flow_id']))
    time.sleep(0.2)
    assert profiles.forgotten == [] and HF_PROFILE in profiles.have


def test_expiry_removes_the_profile_the_flow_created(api, profiles, monkeypatch):
    client, p = api
    monkeypatch.setattr(oauth, 'HOLD_TTL_S', 0.15)
    out = _start_hf(client, p)
    profiles.have.add(out['profile'])
    _sign_in(out)
    assert _wait(lambda: profiles.forgotten == [HF_PROFILE]), profiles.forgotten
    assert len(p.to(HF_REVOKE)) == 2                   # the token was revoked before the profile went


def test_a_sign_in_nobody_finished_removes_it_when_the_flow_times_out(api, profiles, monkeypatch):
    client, p = api
    monkeypatch.setattr(oauth, 'FLOW_TTL_S', 0.15)
    out = _start_hf(client, p)
    profiles.have.add(out['profile'])
    assert _wait(lambda: profiles.forgotten == [HF_PROFILE]), profiles.forgotten


def test_a_vendor_refusal_removes_it(api, profiles):
    client, p = api
    out = _start_hf(client, p)
    profiles.have.add(out['profile'])
    state = _state_of(out['auth_url'])['state']
    _hit(out['redirect_uri'], error='access_denied', state=state)
    assert profiles.forgotten == [HF_PROFILE]


def test_a_save_that_lands_keeps_the_profile(api, profiles):
    client, p = api
    out = _start_hf(client, p)
    profiles.have.add(out['profile'])
    _sign_in(out)
    assert _save(client, HF_URL, held=_held(out)).status_code == 201
    assert profiles.forgotten == [] and HF_PROFILE in profiles.have
    assert _cancel(client, out).get_json() == {'ok': False}       # nothing left to cancel
    assert profiles.forgotten == []


def test_a_replaced_sign_in_removes_the_profile_its_flow_created(api, profiles):
    client, p = api
    first = _start_hf(client, p)
    profiles.have.add(first['profile'])
    _sign_in(first)
    second = _start_hf(client, p)                      # same service: the first is dropped
    assert profiles.forgotten == [HF_PROFILE]
    profiles.have.add(second['profile'])               # the second one's pane writes it again
    assert _cancel(client, second).get_json() == {'ok': True}
    assert profiles.forgotten == [HF_PROFILE, HF_PROFILE]


# -- 2. removing a profile closes its Chromium first --------------------------------------------

def test_forgetting_a_profile_closes_its_pane_before_deleting_the_directory(tmp_path, monkeypatch):
    from mc.blueprints import browser_routes as br
    root = tmp_path / 'named'
    mine, other = root / 'desk-x-abc', root / 'reddit'
    for d in (mine, other):
        (d / 'Default').mkdir(parents=True)
        (d / 'Default' / 'Cookies').write_text('c')
    monkeypatch.setattr(br, '_named_profiles_root', lambda: str(root))
    sessions = {'s1': {'session_id': 's1', 'profile': 'desk-x-abc'}, 's2': {'session_id': 's2', 'profile': 'reddit'}}
    monkeypatch.setattr(br, 'browser_sessions', sessions)
    seen = []

    def kill(session):
        seen.append((session['session_id'], mine.is_dir()))     # the directory must still be there when Chromium closes
    monkeypatch.setattr(br, '_kill_browser_session', kill)
    assert profile_mod.existed('desk-x-abc') and profile_mod.existed('reddit') and not profile_mod.existed('nope')
    assert profile_mod.forget('desk-x-abc') is True
    assert seen == [('s1', True)]
    assert not mine.exists() and (other / 'Default' / 'Cookies').exists()
    assert list(sessions) == ['s2']                              # only the flow's own pane left the registry


def test_forgetting_refuses_a_name_that_is_not_a_profile_name(tmp_path, monkeypatch):
    from mc.blueprints import browser_routes as br
    root = tmp_path / 'named'
    (root / 'keep').mkdir(parents=True)
    monkeypatch.setattr(br, '_named_profiles_root', lambda: str(root))
    assert profile_mod.forget('..') is False and profile_mod.forget('') is False and profile_mod.forget(None) is False
    assert (root / 'keep').is_dir()


# -- 3. the Save takes the hold in one step ----------------------------------------------------

def test_an_expiry_in_the_middle_of_the_write_cannot_revoke_what_is_being_saved(api, monkeypatch):
    client, p = api
    out = _start_hf(client, p)
    _sign_in(out)
    real_store = oauth._store_record

    def store_after_expiry(service, rec, account_id=None):
        hold._expire(out['flow_id'])                   # the timer fires while the Save is writing
        return real_store(service, rec, account_id)
    monkeypatch.setattr(oauth, '_store_record', store_after_expiry)
    r = _save(client, HF_URL, held=_held(out))
    assert r.status_code == 201, r.get_json()
    assert not p.to(HF_REVOKE) and 'oauth.higgsfield' in _vault_names()


def test_an_expired_hold_is_refused_without_a_network_call_on_the_saving_thread(api, monkeypatch):
    client, p = api
    out = _start_hf(client, p)
    _sign_in(out)
    hold._held[out['flow_id']]['expires'] = time.time() - 1       # past its time, its timer not yet fired
    revoked_on = []
    monkeypatch.setattr(oauth, 'revoke_record', lambda *a, **k: revoked_on.append(threading.current_thread()) or True)
    me = threading.current_thread()
    r = _save(client, HF_URL, held=_held(out))
    assert r.status_code == 409 and r.get_json()['code'] == 'hold_missing' and _vault_names() == []
    assert revoked_on == []                            # the Save only refused: its own timer drops it
    with pytest.raises(hold.HoldError):                # the claim itself (the last step of a Save) refuses the same way
        hold.take(out['flow_id'], out['claim'], 'higgsfield')
    assert _wait(lambda: revoked_on)                   # and the late entry is still revoked, just not on this thread
    assert me not in revoked_on and hold.alive(out['flow_id']) == 0


def test_a_failed_write_gives_the_hold_back_with_the_time_it_had_left(api, monkeypatch):
    client, p = api
    out = _start_hf(client, p)
    _sign_in(out)
    before = hold.alive(out['flow_id'])

    def refuse(*_a, **_k):
        raise secrets_store.SecretsError('no')
    monkeypatch.setattr(oauth, '_store_record', refuse)
    assert _save(client, HF_URL, held=_held(out)).status_code == 400
    left = hold.alive(out['flow_id'])
    assert 0 < left <= before and _vault_names() == []


# -- 4. cancel mid-exchange, eviction ----------------------------------------------------------

def test_a_cancel_while_the_code_is_being_exchanged_holds_nothing(api, profiles):
    client, p = api
    out = _start_hf(client, p)
    profiles.have.add(out['profile'])
    real = oauth._token_response

    def cancel_then_answer(*a, **k):
        assert _cancel(client, out).get_json() == {'ok': True}      # the person backs out during the exchange
        return real(*a, **k)
    oauth._token_response = cancel_then_answer
    try:
        _sign_in(out)
    finally:
        oauth._token_response = real
    assert hold.alive(out['flow_id']) == 0
    assert len(p.to(HF_REVOKE)) == 2                   # what the vendor minted is revoked at once
    assert profiles.forgotten == [HF_PROFILE]          # and the profile goes once, not twice
    assert _vault_names() == []


def test_evicting_a_flow_drops_its_held_sign_in_at_once(api, profiles, monkeypatch):
    client, p = api
    monkeypatch.setattr(oauth, 'MAX_FLOWS', 1)
    first = _start_hf(client, p)
    profiles.have.add(first['profile'])
    _sign_in(first)
    assert hold.alive(first['flow_id'])
    second = _start_x(client, p)                       # another service: no supersede, the cap evicts the oldest
    assert second['flow_id'] != first['flow_id']
    assert hold.alive(first['flow_id']) == 0
    assert len(p.to(HF_REVOKE)) == 2 and profiles.forgotten == [HF_PROFILE]


# -- 5. the app the sign-in was minted for -----------------------------------------------------

def test_a_sign_in_minted_for_another_x_app_is_refused_before_anything_is_written(api):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)
    r = _save(client, X_URL, fields={**X_FIELDS, 'client_id': 'someone-elses-app'}, held=_held(out))
    assert r.status_code == 409 and r.get_json()['code'] == 'app_mismatch'
    assert _vault_names() == [] and _accounts() == {} and hold.alive(out['flow_id'])
    ok = _save(client, X_URL, fields=X_FIELDS, held=_held(out), rid='req-held-0002')
    assert ok.status_code == 201, ok.get_json()


def test_commit_held_itself_refuses_a_different_app_and_gives_the_hold_back(api):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)

    class _Undo:
        def push(self, *_a):
            raise AssertionError('nothing was written, so nothing is undone')
    with pytest.raises(oauth.OAuthError) as e:
        oauth.commit_held('x', out['flow_id'], out['claim'], None, _Undo(), client_id='someone-elses-app')
    assert e.value.code == 'app_mismatch' and hold.alive(out['flow_id']) and _vault_names() == []


def test_the_x_save_uses_the_stored_client_id_when_the_form_has_none(api):
    client, p = api
    secrets_store.set_secret('x.client-id', CLIENT_ID, description='x', scope='global', allow_unattended=True,
                             entry_type=secrets_store.ENTRY_API_KEY)
    assert oauth.save_client_id('x', {}) == CLIENT_ID and oauth.save_client_id('x', {'client_id': 'typed'}) == 'typed'
    assert oauth.save_client_id('higgsfield', {'client_id': 'ignored'}) is None


# -- 6. a locked vault opens nothing ----------------------------------------------------------

@pytest.mark.parametrize('service,body', [('higgsfield', {}), ('x', {'hold': {'client_id': CLIENT_ID}})])
def test_start_held_on_a_locked_vault_refuses_before_anything_opens(api, monkeypatch, service, body):
    client, p = api
    monkeypatch.setattr(secrets_store, 'is_locked', lambda: True)

    def opened(*_a, **_k):
        raise AssertionError('a locked vault must be reported before a listener, discovery or sign-in page opens')
    monkeypatch.setattr(oauth, '_open_listener', opened)
    monkeypatch.setattr(oauth, '_discover_higgsfield', opened)
    r = client.post(f'/api/desk/connect/{service}/start-held', json={'passcode': PASSCODE, **body})
    assert r.status_code == 409 and r.get_json()['code'] == 'vault_locked'
    assert p.calls == [] and not oauth._flows and not hold._held
