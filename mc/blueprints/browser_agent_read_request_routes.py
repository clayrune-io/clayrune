"""Answering an agent's read request card (backlog b1e1b23c, MC-1059 piece A).

  GET  /api/browser/agent-read/requests/<id>            what a card shows (agent, profile, site,
                                                        state). Metadata only, never a page.
  POST /api/browser/agent-read/requests/<id>/decision   {decision: allow_once | always | ignore,
                                                        passcode}
                                                        HUMAN ONLY, the same two checks as the
                                                        policy PUT (browser_agent_read_routes):
                                                        an unattended caller is refused, then
                                                        the dashboard passcode must be retyped
                                                        in the body. `ignore` grants nothing and
                                                        needs no passcode, but an unattended
                                                        caller is still refused.

  allow_once  mints a single-use pass for profile + site + the asking session, good for 15
              minutes and spent by the next successful read it covers.
  always      adds the site to the profile's agent-read list (switching the profile on if it was
              off), exactly what the Desk's list editor would store.
  ignore      dismisses the card; nothing is stored.

The request itself is opened only by a refused `read-digest` (mc/browser_agent_read_ask.py);
there is no route here that creates one, and none that an agent can use to grant one.
"""
from flask import Blueprint, jsonify, request

from mc import browser_agent_read as policy
from mc import browser_agent_read_requests as store
from mc.blueprints import browser_routes as br
from mc.blueprints.mcp_write_gate import refuse_unattended, require_passcode
from mc.core import _log

bp = Blueprint('browser_agent_read_request_routes', __name__)

_DECISIONS = {'allow_once': store.ALLOWED_ONCE, 'always': store.ALWAYS, 'ignore': store.IGNORED}


@bp.route('/api/browser/agent-read/requests/<rid>', methods=['GET'])
def agent_read_request_get(rid):
    rec = store.public_request(rid)
    if rec is None:
        return jsonify({'error': 'this request has expired or is unknown'}), 404
    return jsonify(rec)


@bp.route('/api/browser/agent-read/requests/<rid>/decision', methods=['POST'])
def agent_read_request_decide(rid):
    refused = refuse_unattended('answer a read request')
    if refused:
        return refused
    data = request.get_json(silent=True) or {}
    decision = data.get('decision')
    if decision not in _DECISIONS:
        return jsonify({'error': 'decision must be allow_once, always or ignore'}), 400
    rec = store.get_request(rid)
    if rec is None:
        return jsonify({'error': 'this request has expired or is unknown'}), 404
    if rec['state'] != store.PENDING:
        return jsonify({'error': f"this request was already answered ({rec['state']})",
                        'state': rec['state']}), 409
    if decision != 'ignore':
        if not br.named_profile_exists(rec['profile']):
            return jsonify({'error': f"no saved profile '{rec['profile']}'"}), 404
        refused = require_passcode(data)
        if refused:
            return refused
    if decision == 'always':
        try:
            policy.allow_domain(rec['profile'], rec['domain'])
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
    settled = store.settle(rid, _DECISIONS[decision])
    if settled is None:
        return jsonify({'error': 'this request was already answered'}), 409
    if decision == 'allow_once':
        store.grant_once(rec['profile'], rec['domain'], rec['session_id'])
    _log(f"[browser-agent-read] request {rid} answered: {decision} profile={rec['profile']} "
         f"host={rec['domain']}", flush=True)
    return jsonify({'ok': True, 'state': settled['state']})
