"""Sign in with a saved login (`mc/desk_connect/signin_fill.py`, slice P2b of
docs/DESK_SERVICE_PROFILES_SPEC.md). Its own blueprint: a new concern does not grow
`desk_connect_routes.py` or `browser_routes.py`.

    POST /api/desk/connect/signin/options      {service}  Read-only: the service's sign-in routes and the
                                               stored logins that could be used (names), no values.
    POST /api/desk/connect/signin/fill         {service, route_id, account_id | login, profile?, passcode}
                                               Types a saved login into the sign-in page open in the
                                               browser pane. Needs the dashboard passcode on every
                                               call (the one proof an agent cannot forge) and an
                                               unattended caller is refused first. With `account_id`
                                               the login is the one saved for that account and route,
                                               not a name from the request. No value in the request
                                               or the response; the answer is a state word.
    POST /api/desk/connect/signin/store-login  {service, route_id, new_login, passcode}
                                               The Save of a NEW login for a sign-in route that has no
                                               purposes screen (Higgsfield). One vault entry, username
                                               and password together, written only after the
                                               passcode. (On the purposes screen a new login rides
                                               that screen's own Save instead.)

`fill` and `store-login` refuse an unattended caller first, then check the shape before the passcode,
so a bad body costs no guess.

Scope is derived here, never taken from the request: the Desk is workspace-wide, so a Desk sign-in
uses only GLOBAL-scope logins (`_SCOPE = None`). A body's `project_id` is ignored; naming a project
would otherwise be all it took to use that project's login.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import signin_fill as _fill
from mc.desk_connect import signin_login_store as _logins
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_signin_routes', __name__)

_SCOPE = None            # no project: the Desk is workspace-wide (see the module docstring)


def _refuse_agent(what: str):
    return jsonify({'error': f'this action needs a human: an unattended agent session cannot {what}', 'code': 'human_required'}), 403


def _s(v) -> str:
    return v if isinstance(v, str) else ''


def _body() -> dict:
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


@bp.route('/api/desk/connect/signin/options', methods=['POST'])
def signin_options():
    d = _body()
    try:
        return jsonify(_fill.options(_s(d.get('service')), _SCOPE))
    except _fill.FillError as e:
        return jsonify({'ok': False, 'error': str(e), 'code': e.code}), e.status


@bp.route('/api/desk/connect/signin/fill', methods=['POST'])
def signin_fill():
    unattended = is_unattended_caller()
    if unattended:
        return _refuse_agent('sign a service in')
    d = _body()
    service, route_id = _s(d.get('service')), _s(d.get('route_id'))
    try:
        login, profile = _fill.pick_login(service, route_id, d.get('account_id'), d.get('login'), d.get('profile'))
    except _fill.FillError as e:
        return jsonify({'ok': False, 'error': str(e), 'code': e.code}), e.status
    refusal = _require_human_passcode(d)      # an agent can forge the Origin header and sit in a manual session; it cannot know this
    if refusal is not None:
        return refusal
    try:
        out = _fill.fill(service, route_id, login, profile, project_id=_SCOPE, unattended=unattended)
    except _fill.FillError as e:
        _log(f'[desk_signin] {service}/{route_id} refused: {e.code}', flush=True)
        return jsonify({'ok': False, 'error': str(e), 'code': e.code}), e.status
    except Exception as e:
        _log(f'[desk_signin] unexpected failure ({type(e).__name__})', flush=True)
        return jsonify({'ok': False, 'error': 'could not sign in; see the server log', 'code': 'failed'}), 500
    _log(f'[desk_signin] {service}/{route_id} state={out.get("state")}', flush=True)
    return jsonify(out)


@bp.route('/api/desk/connect/signin/store-login', methods=['POST'])
def signin_store_login():
    if is_unattended_caller():
        return _refuse_agent('store a login')
    d = _body()
    try:
        _fill.route_signin(_s(d.get('service')), _s(d.get('route_id')))
        login = _logins.clean(d.get('new_login'))
        _logins.check_free(login)
    except (_fill.FillError, _logins.LoginError) as e:
        return jsonify({'ok': False, 'error': str(e), 'code': e.code}), e.status
    refusal = _require_human_passcode(d)
    if refusal is not None:
        return refusal
    try:
        _logins.write(login)
    except _logins.LoginError as e:
        return jsonify({'ok': False, 'error': str(e), 'code': e.code}), e.status
    except Exception as e:
        _log(f'[desk_signin] storing a login failed ({type(e).__name__})', flush=True)
        return jsonify({'ok': False, 'error': 'could not store the login; see the server log', 'code': 'failed'}), 500
    return jsonify({'ok': True, 'login': _logins.public(login)}), 201
