"""Human card re-sign-in: confirm X's user once, then claim the held token.

The start route stamps the existing account id after its passcode/unattended gates.
Wizard flows carry no stamp. Never log tokens, provider bodies or exception text.
"""
from __future__ import annotations

import re

from mc import desk_oauth as oauth
from mc.core import _log
from mc.desk_connect import x_account_attach
from mc.desk_connect.undo import UndoStack

ME_URL = 'https://api.x.com/2/users/me'
UNCONFIRMED = 'The X identity could not be confirmed. Nothing was saved. You can save this sign-in manually or discard it.'


def confirm(flow: dict, rec: dict) -> None:
    """One bounded lookup, before the token is held. No stamp means no request."""
    aid = (flow.get('hold') or {}).get('auto_save_account_id')
    if flow['service'] != 'x' or not aid:
        return
    flow['card_signin'] = {'save_allowed': True, 'message': UNCONFIRMED, 'matched': False}
    try:
        target = x_account_attach.target('x', aid)
        st, body = oauth._call('GET', ME_URL, headers={'Authorization': f"Bearer {rec['access_token']}"})
        data = body.get('data') if isinstance(body, dict) else None
        username = data.get('username') if isinstance(data, dict) else None
        if st != 200 or not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,15}', username):
            _log(f'[x-card-signin] identity lookup refused or malformed (HTTP {st})', flush=True)
            return
        expected = target['identity'].strip().lstrip('@')
        match = username.casefold() == expected.casefold()
        flow['card_signin'] = {'save_allowed': match, 'matched': match,
                               'identity': username, 'message': '' if match else
                               f'You signed in as @{username}, but this account is @{expected}. Nothing was saved. Discard this sign-in and sign in to the correct account.'}
    except Exception as e:
        _log(f'[x-card-signin] identity lookup failed ({type(e).__name__})', flush=True)


def require_save_allowed(flow: dict | None) -> None:
    """A known mismatch is refused even if a caller manually submits the claim."""
    card = (flow or {}).get('card_signin') or {}
    if card.get('save_allowed') is False:
        raise oauth.OAuthError('identity_mismatch', card['message'], 409)


def save(flow: dict) -> bool:
    """Reuse the normal claim/app/replacement/undo machinery; failures stay held."""
    card = flow.get('card_signin') or {}
    if not card.get('matched'):
        return False
    undo = UndoStack()
    try:
        # Reserve against cancel/supersede only for the local commit, never the lookup.
        with oauth._lock:
            if flow.get('cancelled') or oauth._flow_by_id(flow['flow_id']) is not flow:
                return False
            flow['saving'] = True
        aid = flow['hold']['auto_save_account_id']
        target = x_account_attach.target('x', aid, identity=card['identity'])
        arg = x_account_attach.oauth_arg(target)
        app = oauth.save_client_id('x', {})
        if not app:
            raise oauth.OAuthError('app_missing', 'The saved X app is no longer available. Save its Client ID before saving this sign-in.', 409)
        if oauth.held_account('x', flow['flow_id'], flow['hold']['claim'], client_id=app) != aid:
            raise oauth.OAuthError('account_mismatch', 'The sign-in belongs to another account.', 409)
        oauth.commit_held('x', flow['flow_id'], flow['hold']['claim'], arg, undo, client_id=app)
        undo.commit()
        oauth.consume_held(flow['flow_id'], keep_result=True)
        card['message'] = 'The X sign-in is saved.'
        return True
    except Exception as e:
        left = undo.unwind()
        _log(f'[x-card-signin] automatic save failed ({type(e).__name__}, undo_left={len(left)})', flush=True)
        if getattr(e, 'code', None) == 'identity_mismatch':
            card['save_allowed'] = False
            card['message'] = 'The saved account identity changed during sign-in. Nothing was saved. Discard this sign-in and start again.'
            return False
        card['message'] = 'The X sign-in could not be saved automatically. It is still held in memory. Save it manually or discard it.'
        if isinstance(e, oauth.OAuthError):
            card['message'] += ' ' + str(e)
        if left:
            card['message'] += ' Some changes could not be undone; check the saved sign-in before retrying.'
        return False
    finally:
        with oauth._lock:
            flow['saving'] = False


def public(flow: dict) -> dict:
    card = flow.get('card_signin')
    return {'save_allowed': card['save_allowed'], 'message': card['message']} if card else {}
