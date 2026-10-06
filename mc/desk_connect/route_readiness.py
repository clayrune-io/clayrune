"""What Clayrune can execute for a route, and how ready one bound route is
(docs/DESK_SERVICE_PROFILES_SPEC.md section 3.3; slice P2).

A profile says what a service OFFERS. This module says what Clayrune can RUN, per
purpose, today. The table below is the one place that says so, keyed by IDs and holding
no body: the code that does the work (`desk_publish`, `desk_engagement`) stays where it
is, and `test_desk_purposes.py` pins each entry to the thing it names.

  * A route Clayrune cannot run for a purpose is still described; it is `bindable` only
    where recording the choice is honest (a browser read: the person chose the pane, and
    the reader for that service is not built yet, which readiness then says).
  * `restricted`, `manual` and MCP routes are never bindable here. A manual route is a
    person finishing the post in their own browser, `restricted` has no flow, and a
    user-supplied MCP is a later slice (U2).
  * Readiness is derived from metadata only (vault names and states, whether a named
    browser profile exists). It opens nothing, signs in to nothing and costs nothing.

Setup states (spec 3.3): `ready`, `needs_signin`, `vault_locked`, `pending_runtime`.
`vault_locked` is a saved sign-in the locked vault cannot open yet: unlock, do not sign in
again. Verification is a different dimension and lives in `purpose_verification`.
"""
from __future__ import annotations

from mc import desk_accounts as _accounts
from mc import desk_engagement as _engagement

BINDABLE_PURPOSES = ('publish', 'read_own')
# A capability is bindable only where the profile has actually said the route covers it.
# `unknown` is not "yes": the binding never infers the missing half (spec 3.3).
COVERED = ('documented', 'claimed')

# (service, route, purpose) -> how Clayrune runs it today.
#   api   a platform API call the Desk already makes (publish or a paid own read)
#   pane  the Desk's browser-pane reader of the account's named profile
EXECUTORS: dict[tuple[str, str, str], str] = {
    ('x', 'x-oauth', 'publish'): 'api',
    ('x', 'x-oauth', 'read_own'): 'api',
    ('x', 'x-browser', 'read_own'): 'pane',
}

# Where the read setting stored per account (`read_via`) comes from.
READ_VIA_OF = {'api': 'api', 'pane': 'pane'}


def executor(service_id: str, route_id: str, purpose: str) -> str | None:
    return EXECUTORS.get((service_id, route_id, purpose))


def route_status(service_id: str, route: dict, purpose: str) -> dict:
    """`{executes, via, bindable, why}` for one route in one purpose. `why` is the plain
    sentence for the screen when the route cannot be bound or cannot run."""
    via = executor(service_id, route['id'], purpose)
    support = route.get('support')
    transport = route.get('transport')
    if purpose not in BINDABLE_PURPOSES:
        return {'executes': False, 'via': None, 'bindable': False,
                'why': 'This purpose is read by the Desk for the whole workspace, not chosen per account.'}
    if support == 'manual':
        return {'executes': False, 'via': None, 'bindable': False,
                'why': 'A person finishes this in their own browser. Clayrune connects to nothing through it.'}
    if support == 'restricted':
        return {'executes': False, 'via': None, 'bindable': False,
                'why': 'Not open: the provider limits it and Clayrune has no flow for it yet.'}
    if transport == 'mcp':
        return {'executes': False, 'via': None, 'bindable': False,
                'why': 'Adding your own MCP server is coming. Clayrune has no adapter that runs this one.'}
    if via:
        return {'executes': True, 'via': via, 'bindable': True, 'why': None}
    if transport == 'browser' and purpose == 'read_own':
        return {'executes': False, 'via': 'pane', 'bindable': True,
                'why': 'The choice is saved. Clayrune cannot read this service through the browser pane yet.'}
    return {'executes': False, 'via': None, 'bindable': False,
            'why': 'Clayrune has no adapter that runs this route yet.'}


def _api_publish(acc: dict) -> dict:
    st = _accounts.publish_state(acc)
    if st['ready']:
        return {'setup': 'ready', 'reason': None}
    return {'setup': 'vault_locked' if st.get('vault_locked') else 'needs_signin', 'reason': st['reason']}


def _api_read(acc: dict) -> dict:
    cap = _engagement.XReader(account_id=acc.get('id')).capability()
    if cap.get('connected'):
        return {'setup': 'ready', 'reason': cap.get('reason')}
    return {'setup': 'vault_locked' if cap.get('vault_locked') else 'needs_signin', 'reason': cap.get('reason')}


def _pane_read(profile: str | None) -> dict:
    cap = _engagement.PaneXReader('', profile or '').capability()
    return {'setup': 'ready' if cap.get('connected') else 'needs_signin', 'reason': cap.get('reason')}


def setup_state(service_id: str, route_id: str, purpose: str, acc: dict, binding: dict) -> dict:
    """`{setup, reason}` of one bound route. Never raises: a reader that cannot answer
    reads as not ready, never as ready."""
    via = executor(service_id, route_id, purpose)
    try:
        if via == 'api' and purpose == 'publish':
            return _api_publish(acc)
        if via == 'api':
            return _api_read(acc)
        if via == 'pane':
            return _pane_read((binding.get('refs') or {}).get('browser_profile'))
    except Exception as e:                       # a status that cannot be read is not "ready"
        return {'setup': 'needs_signin', 'reason': f'its state could not be read ({type(e).__name__})'}
    return {'setup': 'pending_runtime',
            'reason': 'Saved. Clayrune cannot run this route for this purpose yet.'}
