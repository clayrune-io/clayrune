"""The connection check of an approved remote MCP server (docs/DESK_SERVICE_PROFILES_SPEC.md section
6.3, slice U2d): "post-Save initialize/`tools/list` can verify protocol availability; it does not
prove every purpose or endpoint safe". Flask-free; the route holds the human check.

Started by a person, after the Save, for one approved server. It sends exactly three messages to the
APPROVED address through `remote_mcp_transport` (initialize, the `initialized` notification, and
`tools/list` page by page) with the approved credentials, read from the vault here and nowhere else,
and never calls a tool. Before the Save nothing may call this: it takes the stored approval.

What it returns is what was observed, bounded: the server's own name and version, the protocol
version it chose, the names of its capabilities, and for every tool its name and a hash of its whole
definition. The server's text is data; none of it is interpreted or stored beyond that. The result is
handed to `remote_mcp_observed` which compares it with the baseline and clears the handshake check
when the server changed.

`purpose_verified` is always False. Reaching a server proves that it answers; it does not prove that
the server does what a purpose needs.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from mc import secrets_store as _vault
from mc.desk_connect import custom_connection_store as _store
from mc.desk_connect import remote_mcp_observed as _observed
from mc.desk_connect import remote_mcp_transport as _t
from mc.desk_connect.mcp_errors import ActivationError

PROTOCOL_VERSION = '2025-06-18'
DEADLINE_S = 40.0
MAX_PAGES = 10
MAX_TOOLS = 500
_NAME_OK = re.compile(r'^[A-Za-z0-9_.:/-]{1,128}$')
_TOOL_KEYS = ('name', 'title', 'description', 'inputSchema', 'outputSchema', 'annotations')
_VAULT_ERRORS = {
    _vault.VaultLocked: ('vault_locked', 'Secrets is locked. Unlock it, then run the check again.'),
    _vault.SecretNotFound: ('credential_missing', 'A Secrets entry this server needs no longer exists.'),
    _vault.SecretDenied: ('credential_denied', 'Secrets would not hand out a credential this server needs.'),
}


def _text(value, limit: int) -> str:
    """Server text reduced to bounded printable characters (shown escaped, never interpreted)."""
    if not isinstance(value, str):
        return ''
    return ''.join(c for c in value if c.isprintable())[:limit].strip()


def resolve_headers(op: dict, project_id: str | None, get_value=None) -> dict:
    """The request headers for the approved credentials, from the vault. Raises ActivationError
    (no value in any message)."""
    get_value = get_value or _vault.get_secret_value
    headers: dict = {}
    for c in op['credentials']:
        try:
            value = get_value(c['vault'], consumer='desk-remote-check', project_id=project_id, unattended=False)
        except _vault.SecretsError as e:
            code, msg = next((v for k, v in _VAULT_ERRORS.items() if isinstance(e, k)),
                             ('credential_unavailable', 'A Secrets entry this server needs could not be read.'))
            raise ActivationError(msg, code, 409) from None
        headers[c['header']] = c['prefix'] + value
    return headers


class _Rpc:
    """Sends requests and waits for their answers, whether they arrive in the POST response
    (Streamable HTTP) or on the event stream (SSE)."""

    def __init__(self, deadline: float):
        self._deadline = deadline
        self._cond = threading.Condition()
        self._answers: dict = {}
        self._next = 0
        self.session: Any = None

    def on_message(self, m: dict) -> None:
        if 'id' in m and 'method' not in m:
            with self._cond:
                self._answers[m['id']] = m
                self._cond.notify_all()

    def call(self, method: str, params: dict | None = None) -> dict:
        self._next += 1
        rid = f'check-{self._next}'
        msg: dict = {'jsonrpc': '2.0', 'id': rid, 'method': method}
        if params is not None:
            msg['params'] = params
        self.session.send(msg)
        with self._cond:
            while rid not in self._answers:
                left = self._deadline - time.monotonic()
                if left <= 0:
                    raise _t.TransportError('timeout', 'The server did not answer in time.')
                self._cond.wait(min(left, 1.0))
            return self._answers.pop(rid)


def _tool_entry(tool) -> dict | None:
    if not isinstance(tool, dict) or not isinstance(tool.get('name'), str):
        return None
    body = {k: tool[k] for k in _TOOL_KEYS if k in tool}
    h = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True, default=str)
                       .encode('ascii')).hexdigest()
    name = tool['name'] if _NAME_OK.match(tool['name']) else '(name not shown)'
    return {'name': name, 'hash': f'sha256:{h}'}


def run(op: dict, headers: dict, *, resolver=None, timeout: float = 30.0) -> dict:
    """Connect, initialize and list tools; return the bounded observation. Raises
    `remote_mcp_transport.TransportError` (a refusal or failure, no header value in it)."""
    deadline = time.monotonic() + DEADLINE_S
    rpc = _Rpc(deadline)
    session = _t.open_session(op['protocol'], op['url'], headers, rpc.on_message,
                              allow_private=op['exposure']['local_or_private'], timeout=timeout, resolver=resolver,
                              deadline=deadline)
    rpc.session = session
    try:
        init = rpc.call('initialize', {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {},
                                       'clientInfo': {'name': _t.CLIENT_NAME, 'version': '1'}})
        res = init.get('result')
        if not isinstance(res, dict) or not isinstance(res.get('protocolVersion'), str):
            raise _t.TransportError('bad_message', 'The server did not answer the opening request as an MCP server does.')
        session.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        caps: dict = {}
        if isinstance(res.get('capabilities'), dict):
            caps = res['capabilities']
        info: dict = {}
        if isinstance(res.get('serverInfo'), dict):
            info = res['serverInfo']
        tools: list[dict] = []
        truncated = False
        if 'tools' in caps:
            cursor = None
            for _ in range(MAX_PAGES):
                ans = rpc.call('tools/list', {'cursor': cursor} if cursor else {})
                page = ans.get('result')
                if not isinstance(page, dict) or not isinstance(page.get('tools'), list):
                    raise _t.TransportError('bad_message', 'The server answered the tool list in a form that is not MCP.')
                for t in page['tools']:
                    e = _tool_entry(t)
                    if e is not None:
                        tools.append(e)
                cursor = page.get('nextCursor') if isinstance(page.get('nextCursor'), str) else None
                if len(tools) > MAX_TOOLS:
                    tools, truncated = tools[:MAX_TOOLS], True
                    break
                if not cursor:
                    break
            else:
                truncated = True
    finally:
        session.close()
    tools.sort(key=lambda t: (t['name'], t['hash']))
    return {'at': datetime.now(timezone.utc).isoformat(), 'protocol_version': res['protocolVersion'][:32],
            'server': {'name': _text(info.get('name'), 80), 'version': _text(info.get('version'), 40)},
            'capabilities': sorted(_text(k, 40) for k in caps)[:20], 'tools': tools, 'truncated': truncated}


# ── the approved server a request names ──────────────────────────────────────

_PROJECT_ID = re.compile(r'^[A-Za-z0-9_.-]{1,120}$')


def _approved(body, allowed: set) -> dict:
    """The stored approval of the remote server a request names, or ActivationError."""
    if not isinstance(body, dict) or set(body) - allowed:
        raise ActivationError('that request has fields it should not have', 'invalid', 400)
    name, scope, pid = body.get('server_name'), body.get('scope'), body.get('project_id')
    if not isinstance(name, str) or not name or scope not in ('project', 'global') \
            or (scope == 'project' and not (isinstance(pid, str) and _PROJECT_ID.match(pid))):
        raise ActivationError('name the server, its reach and (for a project server) its project', 'invalid', 400)
    try:
        rec = _store.get(scope, pid if scope == 'project' else None, name)
    except _store.StoreUnreadable:
        raise ActivationError('the record of approved MCP servers could not be read', 'record_unreadable', 500) from None
    if not rec or rec['operation'].get('ecosystem') != 'remote':
        raise ActivationError('there is no approved remote MCP server of that name', 'not_found', 404)
    return rec


def _view(rec: dict) -> dict:
    """What a person sees of a record: names, counts and the diff, never the tool definitions."""
    latest = rec['latest']
    return {'status': rec['status'], 'checks': rec.get('checks') or {}, 'diff': rec.get('diff') or [],
            'review_needed': rec['status'] == 'changed', 'checked_at': rec.get('checked_at'),
            'observed': _observed.digest_of(latest), 'note': rec.get('note'),
            'server': latest['server'], 'protocol_version': latest['protocol_version'],
            'capabilities': latest['capabilities'], 'tools': [t['name'] for t in latest['tools']],
            'tool_count': len(latest['tools']), 'truncated': latest['truncated']}


def check_approved(body, *, resolver=None, get_value=None) -> dict:
    """Run the check for the approved remote server `body` names (`{server_name, scope, project_id}`)
    and record what it saw. Raises ActivationError for a request or credential problem and
    `remote_mcp_transport.TransportError` for a refusal or failure of the connection itself."""
    rec = _approved(body, {'server_name', 'scope', 'project_id'})
    op = rec['operation']
    if op['auth']['type'] == 'oauth':
        raise ActivationError('This server signs in with OAuth, which this version does not start, so there is no '
                              'credential to check with.', 'oauth_pending', 409)
    headers = resolve_headers(op, rec['project_id'], get_value)
    observed = run(op, headers, resolver=resolver)
    saved = _observed.record(rec['scope'], rec['project_id'], rec['server_name'], rec['fingerprint'], observed)
    return {'ok': True, 'server_name': rec['server_name'], 'url': op['url'], **_view(saved),
            'purpose_verified': False,
            'meaning': 'The server answered, and the tools it offers are recorded. This does not show that it does what '
                       'you want it for.'}


def check_adopt(body) -> tuple[dict, str]:
    """`(approval, observed digest)` for an adopt request, shape-checked. Called BEFORE the passcode, so
    a request for something that does not exist costs no passcode guess. Raises ActivationError."""
    rec = _approved(body, {'server_name', 'scope', 'project_id', 'observed', 'passcode'})
    digest = body.get('observed')
    if not isinstance(digest, str) or not re.match(r'^sha256:[0-9a-f]{64}$', digest):
        raise ActivationError('observed is not valid', 'invalid', 400)
    return rec, digest


def adopt_approved(body) -> dict:
    """A human accepts the changed observation they were shown (`{server_name, scope, project_id,
    observed}`) as the new baseline. The caller has checked the human and the passcode."""
    rec, digest = check_adopt(body)
    saved = _observed.adopt(rec['scope'], rec['project_id'], rec['server_name'], rec['fingerprint'], digest)
    if saved is None:
        raise ActivationError('there is nothing to adopt: the server has not changed since its last check, or it changed '
                              'again after you looked. Run the check again.', 'nothing_to_adopt', 409)
    return {'ok': True, 'server_name': rec['server_name'], **_view(saved), 'purpose_verified': False}


def observation_of(rec: dict) -> dict:
    """The observation summary a list row shows for a remote approval."""
    return _observed.summary(_observed.get(rec['scope'], rec['project_id'], rec['server_name'], rec['fingerprint']))
