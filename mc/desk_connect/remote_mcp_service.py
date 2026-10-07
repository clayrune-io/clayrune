"""The Review of a user-chosen REMOTE MCP server (docs/DESK_SERVICE_PROFILES_SPEC.md section 6.3,
slice U2d). Flask-free; `mc/blueprints/desk_connect_remote_routes.py` holds the human check.

`prepare` is the remote counterpart of `custom_connection_service.prepare`: it validates what the
person typed (`remote_mcp_operation.clean_fields`), builds the operation on the server, remembers it
under a server-made `request_id` and returns the approval card. The Save is the SAME one as for an
npm package (`custom_connection_service.commit` through `POST /api/desk/connect/custom/commit`): it
takes only `{request_id, fingerprint, passcode}` and runs the operation Review stored.

Review makes no request to the address: no DNS lookup, no consent probe, no initialize. The
protocol is proposed from the text of the address alone. What the server actually is stays unknown
until the person saves and runs the check (`remote_mcp_check`), and the card says so.
"""
from __future__ import annotations

import secrets
import time

from mc.desk_connect import custom_connection_service as _service
from mc.desk_connect import custom_connection_store as _store
from mc.desk_connect import remote_mcp_activation as _activation
from mc.desk_connect import remote_mcp_operation as _op
from mc.desk_connect.mcp_errors import ActivationError

_EXPOSURE = {'unencrypted_connection': {'code': 'unencrypted_connection',
                                        'label': 'Not encrypted: the address is http://. Anyone on the network path can read '
                                                 'or change what is sent, including a token.'},
             'local_or_private_target': {'code': 'local_or_private_target',
                                         'label': 'Local or private address: the server is on this computer or a private '
                                                  'network, so it can reach things the internet cannot.'}}


def _risks(op: dict) -> list[dict]:
    out = [{'code': 'user_supplied', 'label': 'User supplied; not reviewed by Clayrune'},
           {'code': 'remote_server_can_change', 'label': 'Remote server can change without a version pin. Its tools and '
                                                         'behavior are whatever it serves when it is contacted.'}]
    if op['credentials']:
        out.append({'code': 'secrets_to_remote', 'label': f'The Vault entries listed are sent as HTTP headers to '
                                                          f'{op["origin"]} and to no other address.'})
    for flag in _op.required_ack(op):
        out.append(_EXPOSURE[flag])
    if op['scope']['kind'] == 'global':
        out.append({'code': 'global_reach', 'label': "Global: agents in EVERY project can use this server's tools."})
    out.append({'code': 'not_purpose_verified', 'label': 'Reaching the server shows that it answers. It does not show that '
                                                         'it does what you want it for.'})
    return out


def _card(request_id: str, fp: str, op: dict, project: dict | None, previous: dict | None, proposal: dict) -> dict:
    reach = ({'scope': 'project', 'project': {'id': project['id'], 'name': project['name']},
              'who': f'Agents working in the project "{project["name"]}" only.'} if project else
             {'scope': 'global', 'project': None, 'who': 'Agents in every project.'})
    changes = _op.changes(previous['operation'] if previous else None, op)
    required, missing = _op.required_ack(op), _op.missing_ack(op)
    return {
        'schema': 'desk-custom-card/1', 'kind': 'remote', 'request_id': request_id, 'fingerprint': fp,
        'origin': {'code': 'user_supplied', 'label': 'User supplied; not reviewed by Clayrune'},
        'title': op['origin'], 'server_name': op['server_name'], 'protocol': op['protocol'],
        'protocol_proposed': proposal, 'url': op['url'], 'recipient': op['origin'],
        'auth': dict(op['auth']),
        'command': _activation.describe_argv(op),
        'credentials': [{'header': c['header'], 'vault': c['vault'], 'prefix': c['prefix'], 'env': c['env'],
                         'placement': f'HTTP header {c["header"]}', 'recipient': op['origin']} for c in op['credentials']],
        'exposure': {'unencrypted': op['exposure']['unencrypted'], 'local_or_private': op['exposure']['local_or_private'],
                     'required': required, 'acknowledged': list(op['exposure']['acknowledged']), 'missing': missing},
        'reach': {**reach, 'secrets_to': op['origin'] if op['credentials'] else 'none'},
        'scope_default': 'project', 'scope_options': ['project', 'global'], 'scope_chosen': op['scope']['kind'],
        'contact': {'before_save': 'none',
                    'text': 'Nothing has been sent to this server: no connection, no consent request, no initialize. The '
                            'protocol above is a proposal from the address alone.'},
        'verification': {'purpose_verified': False,
                         'text': 'Saving does not check that the server works. A check you start after saving can confirm '
                                 'it answers and records the tools it offers; it never counts as verifying a purpose.'},
        'risks': _risks(op), 'limitations': _activation.limitations(op),
        'changes': changes, 'reask': bool(previous and previous['fingerprint'] != fp),
        'replaces': previous['fingerprint'] if previous and previous['fingerprint'] != fp else None,
        'approved': False,
    }


def prepare(body, resolve_project) -> dict:
    """The Review card for the remote server a person typed. `resolve_project(project_id)` returns
    `{id, name, path}` or raises ActivationError. Raises ActivationError. No network."""
    if not isinstance(body, dict):
        raise ActivationError('the request must be an object', 'invalid', 400)
    scope = body.get('scope', 'project')
    pid = body.get('project_id')
    if scope == 'project' and isinstance(pid, str) and not _service._PROJECT_ID.match(pid):
        raise ActivationError('that project id is not valid', 'bad_project', 400)
    vault_names = _service.visible_vault_names(scope, pid if isinstance(pid, str) else None)
    raw = dict(body)
    url = _op.clean_url(raw.get('url'))
    proposal = _op.propose_protocol(url)
    if raw.get('protocol') in (None, ''):
        raw['protocol'] = proposal['value']
    fields = _op.clean_fields(raw, vault_names)
    project = resolve_project(fields['project_id']) if fields['scope'] == 'project' else None
    op = _op.build(fields)
    fp = _op.fingerprint(op)
    clash = _activation.conflict(op, project['path'] if project else None)
    if clash:
        raise clash
    try:
        previous = _store.get(op['scope']['kind'], op['scope']['project_id'], op['server_name'])
    except _store.StoreUnreadable:
        previous = None
    request_id = secrets.token_urlsafe(24)
    _service._remember(request_id, {'op': op, 'fingerprint': fp, 'project': project,
                                    'expires': time.monotonic() + _service.PREPARED_TTL_S})
    return _card(request_id, fp, op, project, previous, proposal)
