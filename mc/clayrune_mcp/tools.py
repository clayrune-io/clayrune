"""The eight `clayrune` MCP tools: schemas, argument checks, bounded forwarding.

Stdlib only; this module runs inside the MCP child process.

Every tool forwards to an EXISTING Clayrune route on loopback. Nothing here
adds delivery behaviour:

  * one attempt per call, no retry; a timeout reports "outcome unknown" and
    says so, because a POST that timed out may still have landed;
  * `clayrune_send_message` reports exactly what /agent/send answered
    (`queued`, `route`, HTTP status) and does not call that "delivered";
  * `clayrune_start_job` is the existing /agent/<sid>/job route, so its
    timeout cap, incognito refusal, Codex-sandbox 403 and (for unattended
    sessions) its fence check stay authoritative;
  * `clayrune_send_message` / `clayrune_dispatch_agent` first ask the server
    whether the unattended fence would block that text sent the curl way
    (`/api/clayrune-mcp/fence-check`; see `fence_parity.py`) and refuse if so.

Identity: the project is fixed at launch (`Gateway.project_id`). The calling
SESSION is never an argument. Mutating / owner-scoped tools ask the server
(`POST /api/clayrune-mcp/identity`) who is calling on every use, and refuse
unless the server confirms a live session in this project. Argument names that
would let a caller claim an identity (`session_id`, `project_id`,
`notify_session`, `agent`) are rejected outright, not ignored.
"""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from mc.clayrune_mcp.protocol import INVALID_PARAMS, RpcError

MAX_RESPONSE_BYTES = 256 * 1024     # a Clayrune reply larger than this is refused
MAX_RESULT_CHARS = 32 * 1024        # text handed back to the model
DEFAULT_TIMEOUT_S = 15.0
IDENTITY_TIMEOUT_S = 10.0
_LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}
_ID_RE = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')

# Names that would let a caller speak for a session. The schemas do not declare
# them; this list only turns the generic "unknown argument" into an explanation.
_IDENTITY_ARGS = ('session_id', 'project_id', 'notify_session', 'agent', 'caller', 'sender')


class ToolError(Exception):
    """A tool-level failure: returned to the model as an `isError` result."""

    def __init__(self, message: str, data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.data = data or {}


@dataclass
class HttpReply:
    status: int
    data: Any


@dataclass
class Caller:
    session_id: str
    label: str


class Gateway:
    """Loopback HTTP to one Clayrune server, scoped to one project."""

    def __init__(self, base_url: str, project_id: str, *,
                 opener: Optional[Callable[..., Any]] = None,
                 timeout: float = DEFAULT_TIMEOUT_S):
        parsed = urllib.parse.urlparse(base_url)
        host = (parsed.hostname or '').lower()
        if parsed.scheme != 'http' or host not in _LOOPBACK_HOSTS or not parsed.port:
            raise ValueError('base URL must be http://<loopback>:<port>')
        if not _ID_RE.match(project_id or ''):
            raise ValueError('a valid --project id is required')
        self.base_url = f'http://{parsed.netloc}'
        self.project_id = project_id
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout

    # -- transport -----------------------------------------------------
    def request(self, method: str, path: str, *, body: Optional[Dict[str, Any]] = None,
                query: Optional[Dict[str, Any]] = None,
                timeout: Optional[float] = None) -> HttpReply:
        url = self.base_url + path
        if query:
            url += '?' + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        try:
            with self._opener(req, timeout=timeout or self._timeout) as resp:
                return self._reply(resp.status, resp.read(MAX_RESPONSE_BYTES + 1))
        except urllib.error.HTTPError as e:
            return self._reply(e.code, e.read(MAX_RESPONSE_BYTES + 1))
        except (socket.timeout, TimeoutError):
            raise ToolError(
                'no response from Clayrune before the timeout. The request was NOT '
                'retried and its outcome is unknown: it may or may not have been '
                'applied. Check clayrune_delivery_status / clayrune_list_agents before '
                'sending again.', {'outcome': 'unknown'})
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise ToolError('no response from Clayrune before the timeout; outcome '
                                'unknown, not retried.', {'outcome': 'unknown'})
            raise ToolError(f'Clayrune is not reachable ({type(e.reason).__name__}). '
                            'Nothing was sent.', {'outcome': 'not_sent'})

    @staticmethod
    def _reply(status: int, raw: bytes) -> HttpReply:
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ToolError(f'Clayrune reply (HTTP {status}) exceeded '
                            f'{MAX_RESPONSE_BYTES} bytes and was discarded.')
        try:
            return HttpReply(status, json.loads(raw.decode('utf-8')) if raw else None)
        except ValueError:
            return HttpReply(status, {'raw': raw.decode('utf-8', 'replace')[:2000]})

    # -- scoped paths --------------------------------------------------
    def project_path(self, suffix: str) -> str:
        return f'/api/project/{urllib.parse.quote(self.project_id, safe="")}{suffix}'

    # -- identity ------------------------------------------------------
    def caller(self) -> Caller:
        """Ask the server who is calling. Raises ToolError unless it names a
        live session in THIS project. Never cached: it is one loopback call
        and caching would outlive the session it vouched for."""
        reply = self.request('POST', '/api/clayrune-mcp/identity',
                             body={'project_id': self.project_id}, timeout=IDENTITY_TIMEOUT_S)
        data = reply.data if isinstance(reply.data, dict) else {}
        if reply.status != 200 or not data.get('ok'):
            raise ToolError('refused: ' + str(data.get('error') or f'identity check failed (HTTP {reply.status})'),
                            {'outcome': 'not_sent'})
        sid = str(data.get('session_id') or '')
        # Belt and braces: the server already compared, but this process was
        # launched for one project and must not act for another.
        if not sid or data.get('project_id') != self.project_id or not _ID_RE.match(sid):
            raise ToolError('refused: the server returned an identity for a different '
                            'project than this tool server was launched for',
                            {'outcome': 'not_sent'})
        return Caller(sid, str(data.get('label') or sid[:8]))

    def require_fence_clear(self, action: str, text: str, target: str = '') -> None:
        """Refuse unless the server says the unattended fence would let this text
        through the curl way. Fails closed: an unanswered or malformed check is
        a refusal, never a pass."""
        body: Dict[str, Any] = {'project_id': self.project_id, 'action': action, 'text': text}
        if target:
            body['target_session_id'] = target
        reply = self.request('POST', '/api/clayrune-mcp/fence-check', body=body,
                             timeout=IDENTITY_TIMEOUT_S)
        data = reply.data if isinstance(reply.data, dict) else {}
        if reply.status != 200 or data.get('ok') is not True or 'blocked' not in data:
            raise ToolError('refused: the unattended-fence check could not be completed '
                            f'(HTTP {reply.status}); nothing was sent.', {'outcome': 'not_sent'})
        if data.get('blocked'):
            raise ToolError('refused by the unattended fence: ' + str(data.get('reason') or 'blocked')
                            + '. The same text sent with curl would be blocked too; nothing was sent.',
                            {'outcome': 'not_sent', 'fence_blocked': True})


# ── argument validation ──────────────────────────────────────────────────────

def _check_args(schema: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    props: Dict[str, Any] = schema.get('properties', {})
    for key in args:
        if key not in props:
            if key in _IDENTITY_ARGS:
                raise ToolError(f"argument '{key}' is not accepted: the caller's identity is "
                                'bound by the server and cannot be supplied')
            raise ToolError(f"unknown argument '{key}'")
    for key in schema.get('required', []):
        if args.get(key) in (None, ''):
            raise ToolError(f"missing required argument '{key}'")
    clean: Dict[str, Any] = {}
    for key, value in args.items():
        spec = props[key]
        kind = spec.get('type')
        if kind == 'string':
            if not isinstance(value, str):
                raise ToolError(f"'{key}' must be a string")
            if len(value) > spec.get('maxLength', 20000):
                raise ToolError(f"'{key}' is longer than {spec.get('maxLength', 20000)} characters")
            if 'pattern' in spec and not re.match(spec['pattern'], value):
                raise ToolError(f"'{key}' has an invalid format")
            if 'enum' in spec and value not in spec['enum']:
                raise ToolError(f"'{key}' must be one of {spec['enum']}")
        elif kind == 'integer':
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolError(f"'{key}' must be an integer")
        elif kind == 'number':
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ToolError(f"'{key}' must be a number")
        elif kind == 'boolean':
            if not isinstance(value, bool):
                raise ToolError(f"'{key}' must be a boolean")
        elif kind == 'array':
            if not isinstance(value, list) or len(value) > spec.get('maxItems', 50) \
                    or not all(isinstance(v, str) and len(v) <= 500 for v in value):
                raise ToolError(f"'{key}' must be a short list of strings")
        if kind in ('integer', 'number'):
            if 'minimum' in spec and value < spec['minimum']:
                raise ToolError(f"'{key}' must be >= {spec['minimum']}")
            if 'maximum' in spec and value > spec['maximum']:
                raise ToolError(f"'{key}' must be <= {spec['maximum']}")
        clean[key] = value
    return clean


# ── tools ────────────────────────────────────────────────────────────────────

@dataclass
class Tool:
    name: str
    description: str
    properties: Dict[str, Any]
    required: List[str]
    handler: Callable[[Gateway, Dict[str, Any]], Dict[str, Any]]

    @property
    def schema(self) -> Dict[str, Any]:
        return {'type': 'object', 'properties': self.properties,
                'required': self.required, 'additionalProperties': False}

    def listing(self) -> Dict[str, Any]:
        return {'name': self.name, 'description': self.description,
                'inputSchema': self.schema}


def _as_result(reply: HttpReply, **extra: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {'http_status': reply.status, 'ok': 200 <= reply.status < 300}
    out.update(extra)
    out['response'] = reply.data
    return out


def _list_agents(gw: Gateway, args: Dict[str, Any]) -> Dict[str, Any]:
    reply = gw.request('GET', gw.project_path('/coord/roster'))
    you = None
    try:  # marking the caller is a convenience; a refusal must not fail a read
        you = gw.caller().session_id
    except ToolError:
        pass
    return _as_result(reply, you=you)


def _coord_events(gw: Gateway, args: Dict[str, Any]) -> Dict[str, Any]:
    reply = gw.request('GET', gw.project_path('/coord/events'),
                       query={'limit': args.get('limit', 20)})
    return _as_result(reply)


def _delivery_status(gw: Gateway, args: Dict[str, Any]) -> Dict[str, Any]:
    if args.get('event_id'):
        reply = gw.request('GET', gw.project_path('/agent/delegation/status'),
                           query={'event_id': args['event_id']})
    else:
        reply = gw.request('GET', gw.project_path('/agent/delegation/status-list'),
                           query={'limit': args.get('limit', 20), 'offset': args.get('offset', 0)})
    return _as_result(reply, note='Status is evidence of handoff, not proof the task succeeded.')


def _send_message(gw: Gateway, args: Dict[str, Any]) -> Dict[str, Any]:
    me = gw.caller()
    target = args['target_session_id']
    if target == me.session_id:
        raise ToolError('refused: a session cannot message itself')
    gw.require_fence_clear('send_message', args['message'], target)
    body: Dict[str, Any] = {'session_id': target, 'message': args['message']}
    if args.get('request_reply', True):
        body['notify_session'] = me.session_id
    reply = gw.request('POST', gw.project_path('/agent/send'), body=body, timeout=30.0)
    data = reply.data if isinstance(reply.data, dict) else {}
    note = ('Accepted by Clayrune, not confirmation the target has read or acted on it. '
            'No retry was made.')
    if data.get('route') in ('dispatch', 'resume', 'revive', 'revive-non-claude'):
        note += (f" Route was '{data.get('route')}': the target was not a live session, so "
                 'Clayrune started or revived one.')
    return _as_result(reply, queued=data.get('queued'), route=data.get('route'),
                      reply_requested=bool(args.get('request_reply', True)), note=note)


def _dispatch_agent(gw: Gateway, args: Dict[str, Any]) -> Dict[str, Any]:
    me = gw.caller()
    gw.require_fence_clear('dispatch_agent', args['task'])
    body: Dict[str, Any] = {'task': args['task'], 'notify_session': me.session_id}
    for key in ('character', 'provider', 'model', 'effort'):
        if args.get(key):
            body[key] = args[key]
    reply = gw.request('POST', gw.project_path('/agent/dispatch'), body=body, timeout=60.0)
    return _as_result(reply, note='You will be woken when the child finishes. No retry was made.')


def _coord_publish(gw: Gateway, args: Dict[str, Any]) -> Dict[str, Any]:
    me = gw.caller()
    body: Dict[str, Any] = {'type': args['type'], 'summary': args['summary'],
                            'agent': me.label, 'session_id': me.session_id}
    if args.get('targets'):
        body['targets'] = args['targets']
    if args.get('commit_sha'):
        body['commit_sha'] = args['commit_sha']
    reply = gw.request('POST', gw.project_path('/coord/publish'), body=body)
    return _as_result(reply, note='Awareness only: publishing does not wake or interrupt any session.')


def _start_job(gw: Gateway, args: Dict[str, Any]) -> Dict[str, Any]:
    me = gw.caller()
    body = {k: args[k] for k in ('command', 'cwd', 'timeout_minutes', 'shell') if args.get(k) not in (None, '')}
    reply = gw.request('POST', gw.project_path(f'/agent/{urllib.parse.quote(me.session_id, safe="")}/job'),
                       body=body)
    return _as_result(reply)


def _job_status(gw: Gateway, args: Dict[str, Any]) -> Dict[str, Any]:
    me = gw.caller()
    reply = gw.request('GET', gw.project_path(
        f'/agent/{urllib.parse.quote(me.session_id, safe="")}/job/'
        f'{urllib.parse.quote(args["job_id"], safe="")}'))
    return _as_result(reply)


_ID_PROP = {'type': 'string', 'maxLength': 80, 'pattern': r'^[A-Za-z0-9_.-]{1,80}$'}

TOOLS: List[Tool] = [
    Tool('clayrune_list_agents',
         "List the live agent sessions on this project (id, task, status). 'you' is your own "
         'session id when it can be determined.',
         {}, [], _list_agents),
    Tool('clayrune_send_message',
         'Send a message to another session on this project. By default you are woken when it '
         'finishes that turn. Reports what Clayrune answered (queued / route); that is hand-off, '
         'not proof it was read. Not retried.',
         {'target_session_id': dict(_ID_PROP),
          'message': {'type': 'string', 'maxLength': 20000},
          'request_reply': {'type': 'boolean',
                            'description': 'Wake you when the target finishes (default true).'}},
         ['target_session_id', 'message'], _send_message),
    Tool('clayrune_dispatch_agent',
         'Start a new agent session on this project with a task; you are woken with its result. '
         'Uses the normal dispatch rules (routing, fences, lineage).',
         {'task': {'type': 'string', 'maxLength': 20000},
          'character': {'type': 'string', 'maxLength': 120,
                        'description': "Agent type, e.g. 'project:code-reviewer'."},
          'provider': {'type': 'string', 'maxLength': 40},
          'model': {'type': 'string', 'maxLength': 120},
          'effort': {'type': 'string', 'maxLength': 32}},
         ['task'], _dispatch_agent),
    Tool('clayrune_coord_events',
         'Recent coordination events (intentions / completions) for this project. Read-only.',
         {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, [], _coord_events),
    Tool('clayrune_coord_publish',
         'Publish an intention or a completion to the project coordination feed. Awareness only: '
         'it never wakes or interrupts another session.',
         {'type': {'type': 'string', 'enum': ['intention', 'completion']},
          'summary': {'type': 'string', 'maxLength': 2000},
          'targets': {'type': 'array', 'maxItems': 50},
          'commit_sha': {'type': 'string', 'maxLength': 64}},
         ['type', 'summary'], _coord_publish),
    Tool('clayrune_delivery_status',
         'Read-only delegation delivery evidence for this project: one event by id, or a recent list.',
         {'event_id': {'type': 'string', 'maxLength': 120},
          'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
          'offset': {'type': 'integer', 'minimum': 0, 'maximum': 100000}},
         [], _delivery_status),
    Tool('clayrune_start_job',
         'Run a command as a Clayrune-owned background job on YOUR session; its completion is '
         "delivered back into your session. Existing timeout cap, incognito and sandbox refusals apply.",
         {'command': {'type': 'string', 'maxLength': 10000},
          'cwd': {'type': 'string', 'maxLength': 1000},
          'timeout_minutes': {'type': 'number', 'minimum': 0.1, 'maximum': 100000},
          'shell': {'type': 'string', 'enum': ['bash', 'sh', 'powershell', 'cmd']}},
         ['command'], _start_job),
    Tool('clayrune_job_status',
         'Status of a background job started by YOUR session with clayrune_start_job.',
         {'job_id': dict(_ID_PROP)}, ['job_id'], _job_status),
]


class ClayruneToolHost:
    """Binds the tool table to one Gateway; this is what the protocol loop calls."""

    def __init__(self, gateway: Gateway, tools: Optional[List[Tool]] = None):
        self.gateway = gateway
        self._tools = {t.name: t for t in (tools if tools is not None else TOOLS)}

    def list_tools(self) -> List[Dict[str, Any]]:
        return [t.listing() for t in self._tools.values()]

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        tool = self._tools.get(name)
        if tool is None:
            raise RpcError(INVALID_PARAMS, f'unknown tool: {name}')
        try:
            clean = _check_args(tool.schema, arguments)
            result = tool.handler(self.gateway, clean)
            is_error = not result.get('ok', True)
            text = json.dumps(result, indent=2, ensure_ascii=False)
        except ToolError as e:
            is_error = True
            text = json.dumps({'ok': False, 'error': e.message, **e.data}, indent=2, ensure_ascii=False)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + '\n... [truncated]'
        return {'content': [{'type': 'text', 'text': text}], 'isError': is_error}
