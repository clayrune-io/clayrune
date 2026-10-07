"""Turning an approved user-chosen REMOTE operation into a registered MCP server (docs/
DESK_SERVICE_PROFILES_SPEC.md sections 6.3 and 6.5, slice U2d). The remote counterpart of
`custom_connection_activation`, with the same five entry points (`describe_argv`, `limitations`,
`conflict`, `provision`, `derive_state`) so the service can treat the two kinds alike.

Nothing about the remote server is contacted here. What is written to the MCP config is a local
stdio server that starts `tools/remote-mcp-bridge.py` (`remote_mcp_bridge`):

    with a credential   python tools/with-secret.py --raw [--project <id>] --env CLAYRUNE_REMOTE_CRED_n=<vault name> --
                        python -I tools/remote-mcp-bridge.py --protocol P --url U [--allow-private]
                        --credential '{"header":..,"env":..,"prefix":..}'
    without             python -I tools/remote-mcp-bridge.py --protocol P --url U [--allow-private]

so the config holds vault NAMES, header NAMES and the approved address, never a header value; the
bridge receives the token in its environment and enforces the approval (one origin, no redirect,
no address that was not approved). Nothing is registered through the MCP config's own `http`/`sse`
transport, because that client does not give those guarantees.

OAuth is recorded, not executed: the Save keeps the issuer and scopes in the approval and the
state is `pending_runtime` (`oauth_pending`) until a human-started sign-in exists. It is never
reported as Connected.

`registered` means the config holds exactly the approved launch line, the bridge exists and every
credential entry exists: "an agent session can start it", not "the server answers". Reaching the
server is a separate, human-started check (`remote_mcp_check`).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from mc import mcp as _mcp
from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import custom_connection_activation as _npm
from mc.desk_connect import custom_connection_store as _store
from mc.desk_connect import mcp_activation as _base
from mc.desk_connect.mcp_errors import ActivationError

_PENDING_CODES = ('wrapper_missing',)
_BRIDGE_SCRIPT = 'remote-mcp-bridge.py'
OAUTH_NOTE = ('Sign-in with OAuth is not started by this version: the approval keeps the issuer and scopes, and nothing '
              'is registered or contacted until a human-started sign-in exists.')


def bridge_path() -> Path:
    """`tools/remote-mcp-bridge.py`. Refused in a frozen build, which carries neither it nor a `python`."""
    if getattr(sys, 'frozen', False):
        raise ActivationError('this build of Clayrune does not include the program that connects an MCP server over the '
                              'network, so it cannot activate one', 'wrapper_missing', 409)
    p = Path(__file__).resolve().parents[2] / 'tools' / _BRIDGE_SCRIPT
    if not p.is_file():
        raise ActivationError('the remote MCP bridge (tools/remote-mcp-bridge.py) is missing from this install',
                              'wrapper_missing', 409)
    return p


def wrapper_flags(op: dict) -> list[str]:
    """The `with-secret.py` flags up to and including `--`; empty when the server takes no credential."""
    if not op['credentials']:
        return []
    flags = ['--raw']
    if op['scope']['kind'] == 'project':
        flags += ['--project', op['scope']['project_id']]
    for c in op['credentials']:
        flags += ['--env', f'{c["env"]}={c["vault"]}']
    flags.append('--')
    return flags


def bridge_args(op: dict) -> list[str]:
    """The bridge's own arguments: protocol, address, exposure and the header NAMES. No value."""
    out = ['--protocol', op['protocol'], '--url', op['url']]
    if op['exposure']['local_or_private']:
        out.append('--allow-private')
    for c in op['credentials']:
        out += ['--credential', json.dumps({'header': c['header'], 'env': c['env'], 'prefix': c['prefix']},
                                           sort_keys=True, separators=(',', ':'), ensure_ascii=True)]
    return out


def _line(op: dict, python: str, wrapper: str, bridge: str) -> tuple[str, list[str]]:
    flags = wrapper_flags(op)
    tail = [python, '-I', bridge, *bridge_args(op)]
    if flags:
        return python, [wrapper, *flags, *tail]
    return python, ['-I', bridge, *bridge_args(op)]


def launch_config(op: dict) -> dict:
    """The config written for `op`. May raise ActivationError `wrapper_missing`."""
    bridge = str(bridge_path())
    wrapper = str(_base.wrapper_path()) if op['credentials'] else ''
    command, args = _line(op, sys.executable, wrapper, bridge)
    return {'command': command, 'args': args}


def describe_argv(op: dict) -> dict:
    """The exact normalized command the card shows. Real paths when this install can run it;
    otherwise the same line with the missing program named, and `runnable: false`."""
    try:
        cfg = launch_config(op)
        return {'command': cfg['command'], 'args': cfg['args'], 'runnable': True}
    except ActivationError as e:
        command, args = _line(op, sys.executable, '<tools/with-secret.py>', '<tools/remote-mcp-bridge.py>')
        return {'command': command, 'args': args, 'runnable': False, 'why': str(e), 'code': e.code}


def limitations(op: dict) -> list[dict]:
    """What this install cannot do for this saved server today, as `{code, message}`."""
    out = []
    try:
        bridge_path()
        if op['credentials']:
            _base.wrapper_path()
    except ActivationError as e:
        out.append({'code': e.code, 'message': f'{e} You can still save: the approval is kept as pending and nothing is '
                                               f'registered until this is resolved.'})
    if op['auth']['type'] == 'oauth':
        out.append({'code': 'oauth_pending', 'message': OAUTH_NOTE})
    if op['credentials'] and _base.passphrase_backed():
        out.append({'code': 'passphrase_vault', 'message': _base.PASSPHRASE_NOTICE})
    return out


def _norm(p) -> str:
    return str(p).replace('\\', '/')


def matches(cfg, op: dict, strict: bool = True) -> bool:
    """True when an existing server config is exactly `op`'s launch line. The shape is checked whole:
    an `env` block, an extra argument or a different program fails it.

    `strict` (default; "is registered" and the write guard) also requires the interpreter, the
    wrapper and the bridge to be the ones this install would write now. `strict=False` keeps the
    structural test only (any Python, any `tools/with-secret.py`, any `tools/remote-mcp-bridge.py`)
    for ONE use: recognising an earlier approval whose paths moved with the install."""
    if not isinstance(op, dict) or op.get('ecosystem') != 'remote' or not isinstance(cfg, dict) \
            or not set(cfg) <= {'command', 'args', 'type'} or cfg.get('type') not in (None, 'stdio'):
        return False
    cmd, args = cfg.get('command'), cfg.get('args')
    if not isinstance(cmd, str) or not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return False
    try:
        _, want = _line(op, sys.executable, '<w>', '<b>')
    except (KeyError, TypeError):
        return False
    if len(args) != len(want):
        return False
    slots = {i for i, a in enumerate(want) if a in ('<w>', '<b>')}
    py_slots = {i for i, a in enumerate(want) if a == sys.executable}
    if any(args[i] != want[i] for i in range(len(want)) if i not in slots | py_slots):
        return False
    if strict:
        try:
            bridge, wrapper = bridge_path(), (_base.wrapper_path() if op['credentials'] else None)
        except ActivationError:
            return False
        if cmd != sys.executable or any(args[i] != sys.executable for i in py_slots):
            return False
        return all(_npm._same_path(args[i], bridge if want[i] == '<b>' else wrapper) for i in slots)
    if not (cmd == sys.executable or _base._PYTHON_RE.match(_norm(cmd).rsplit('/', 1)[-1])):
        return False
    if any(not (args[i] == sys.executable or _base._PYTHON_RE.match(_norm(args[i]).rsplit('/', 1)[-1])) for i in py_slots):
        return False
    return all(_norm(args[i]).endswith('tools/with-secret.py' if want[i] == '<w>' else f'tools/{_BRIDGE_SCRIPT}')
               for i in slots)


def _approved_ops(op: dict) -> list[dict]:
    """The remote operations Desk approved for this server name: the recorded one and, while a
    change is unfinished, the one it replaces."""
    try:
        rec = _store.get(op['scope']['kind'], op['scope']['project_id'], op['server_name'])
    except _store.StoreUnreadable:
        return []
    return [o for o in (rec.get('operation'), rec.get('replaces')) if isinstance(o, dict)
            and o.get('ecosystem') == 'remote'] if rec else []


def _was_approved(cur, op: dict) -> bool:
    return any(matches(cur, p, strict=False) for p in _approved_ops(op))


def conflict(op: dict, project_path: str | None) -> ActivationError | None:
    """A server of this name that is neither this operation nor the earlier approval of it."""
    cur = _npm._read(op['scope']['kind'], op['scope']['project_id'], op['server_name'], project_path)
    if cur is None or matches(cur, op):
        return None
    return None if _was_approved(cur, op) else _npm._clash(op)


def register(op: dict, project_path: str | None) -> dict:
    """Write the approved launch line. Never overwrites a server it did not approve."""
    cfg = _mcp.normalize_config('stdio', launch_config(op))
    approved = _approved_ops(op)
    name, state = op['server_name'], {'already': False}

    def mutate(servers: dict) -> None:
        cur = servers.get(name)
        if cur is not None:
            if matches(cur, op):
                state['already'] = True
                return
            if not any(matches(cur, p, strict=False) for p in approved):
                raise _npm._clash(op)
        servers[name] = cfg

    try:
        if op['scope']['kind'] == 'global':
            _mcp._write_global_servers(mutate)
        else:
            _mcp._write_project_servers(project_path or '', mutate)
    except ActivationError:
        raise
    except (OSError, ValueError) as e:
        _log(f'[desk_connect] remote MCP registration failed: {type(e).__name__}', flush=True)
        raise ActivationError('the MCP configuration could not be written; see the server log', 'register_failed', 500) from e
    return {'server': name, 'already': state['already']}


def provision(op: dict, project_path: str | None) -> dict:
    """After the durable approval: register the bridge launch line. Never raises. The result is
    `{'state': 'registered'|'pending_runtime'|'setup_failed', 'code', 'message', 'server'?}`."""
    if op['auth']['type'] == 'oauth':
        return {'state': 'pending_runtime', 'code': 'oauth_pending', 'message': f'Saved and approved. {OAUTH_NOTE}'}
    try:
        done = register(op, project_path)
    except ActivationError as e:
        if e.code in _PENDING_CODES:
            return {'state': 'pending_runtime', 'code': e.code,
                    'message': f'Saved and approved. {e} Nothing is registered or contacted yet.'}
        _log(f'[desk_connect] remote MCP {op["server_name"]} registration failed: {e.code}', flush=True)
        return {'state': 'setup_failed', 'code': e.code, 'message': str(e)}
    except Exception as e:
        _log(f'[desk_connect] remote MCP {op["server_name"]} registration raised {type(e).__name__}', flush=True)
        return {'state': 'setup_failed', 'code': 'setup_failed', 'message': 'setup could not finish; see the server log'}
    return {'state': 'registered', 'code': '', 'server': done['server'],
            'message': f'Registered as the MCP server "{done["server"]}". Nothing has been sent to the remote server; '
                       f'it is contacted the first time an agent session uses it, or when you run the check.'}


def derive_state(rec: dict, project_path: str | None) -> dict:
    """The truthful state of a recorded approval right now. Same vocabulary as the npm kind
    (`registered`, `pending_runtime`, `setup_failed`, `changed`, `missing`, `credential_missing`).
    Never raises."""
    op = rec['operation']
    try:
        cur = _npm._read(rec['scope'], rec['project_id'], rec['server_name'], project_path)
    except (OSError, ValueError) as e:
        _log(f'[desk_connect] remote MCP state unreadable: {type(e).__name__}', flush=True)
        return {'state': 'unknown', 'message': 'The MCP configuration could not be read.'}
    if cur is None:
        if rec.get('state') in ('pending_runtime', 'setup_failed'):
            return {'state': rec['state'], 'code': rec.get('code', ''), 'message': 'Approved and saved, but not registered.'}
        return {'state': 'missing', 'message': 'The approved server is no longer in the MCP configuration.'}
    if not matches(cur, op):
        return {'state': 'changed', 'message': 'The MCP configuration no longer matches what was approved. Connect it '
                                               'again and approve the change.'}
    try:
        have = {s['name'] for s in _vault.list_secrets()}
    except Exception as e:
        _log(f'[desk_connect] vault listing failed: {type(e).__name__}', flush=True)
        have = set()
    gone = [c['vault'] for c in op['credentials'] if c['vault'] not in have]
    if gone:
        return {'state': 'credential_missing', 'message': f'The Vault has no entry named {", ".join(gone)}.', 'missing': gone}
    out = {'state': 'registered', 'message': 'Registered. It is contacted the first time an agent session uses it.'}
    if op['credentials'] and _base.passphrase_backed():
        out['notice'] = _base.PASSPHRASE_NOTICE
    return out
