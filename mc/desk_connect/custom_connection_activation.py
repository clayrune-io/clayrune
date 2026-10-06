"""Turning an approved user-chosen operation into a registered MCP server (docs/
DESK_SERVICE_PROFILES_SPEC.md, sections 6.1, 6.2 and 6.5, slice U2a). The custom counterpart
of `mcp_activation`, which stays the reviewed catalogue's and whose wrapper, Node lookup and
vault notice it reuses.

Runs only after the human Save is accepted (the route holds the passcode check). Three steps:

    1. the package    `custom_npm_artifact.install`: the approved archive is downloaded again,
                      held to the approved digest and unpacked into its own digest directory.
    2. the launch     built from the OPERATION alone, never from a request:
                      python tools/with-secret.py --raw --unset NODE_OPTIONS --unset NODE_PATH
                      [--project <id>] [--env VAR=<vault name> ...] -- <node> <dir>/package/<entry> [args]
                      so the config carries vault NAMES, never a value (the same line, and the
                      same reasons, as the catalogue's: `mcp_activation`).
    3. registration   into the global `~/.claude.json` or the project's `.mcp.json`, checked and
                      written under the SAME lock `mc/mcp.py` uses for every config write, so a
                      server that appeared in between is never overwritten.

An existing server of that name is replaced only when it is exactly what Desk approved before
(the earlier approval, now being changed through a new Save, or the one before it when that change
did not finish) or exactly this operation. Anything
else is `server_exists` and stays untouched.

What it will not claim: a server is `registered` only when the config on disk is the approved
launch line, the archive's entry file is on disk and every vault entry it names exists. That is
"an agent session can start it", not "it works": the first start is deferred to a session and
nothing here runs the package. A frozen build (no credential wrapper), a missing Node.js, or
an `ActivationError` from the package step leaves the approval SAVED with an honest state:
`pending_runtime` or `setup_failed`, never Connected.
"""
from __future__ import annotations

import os
import sys

from mc import mcp as _mcp
from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import custom_connection_store as _store
from mc.desk_connect import custom_npm_artifact as _artifact
from mc.desk_connect import custom_package_manifest as _manifest
from mc.desk_connect import mcp_activation as _base
from mc.desk_connect.mcp_errors import ActivationError

_PENDING_CODES = ('wrapper_missing', 'node_missing')


def wrapper_flags(op: dict) -> list[str]:
    """The `with-secret.py` flags between the script and the program, up to and including `--`.
    One definition for what is written and what `matches` accepts."""
    flags = ['--raw', *[a for v in op['strip_env'] for a in ('--unset', v)]]
    if op['scope']['kind'] == 'project':
        flags += ['--project', op['scope']['project_id']]
    for c in op['credentials']:
        flags += ['--env', f'{c["env"]}={c["vault"]}']
    flags.append('--')
    return flags


def launch_config(op: dict) -> dict:
    """The config written for `op`. May raise ActivationError `wrapper_missing` / `node_missing`."""
    node = _base._node()
    return {'command': sys.executable,
            'args': [str(_base.wrapper_path()), *wrapper_flags(op), node, str(_artifact.entry_path(op)), *op['args']]}


def describe_argv(op: dict) -> dict:
    """The exact normalized command the card shows. Real paths when this machine can run it;
    otherwise the same line with the missing piece named (`node`, the wrapper script), and
    `runnable: false`."""
    try:
        cfg = launch_config(op)
        return {'command': cfg['command'], 'args': cfg['args'], 'runnable': True}
    except ActivationError as e:
        entry = str(_artifact.entry_path(op))
        return {'command': sys.executable,
                'args': ['<tools/with-secret.py>', *wrapper_flags(op), 'node', entry, *op['args']],
                'runnable': False, 'why': str(e), 'code': e.code}


def limitations() -> list[dict]:
    """What this install cannot do for a saved server today, as `{code, message}`."""
    out = []
    try:
        _base.wrapper_path()
    except ActivationError as e:
        out.append({'code': e.code, 'message': f'{e} You can still save: the approval is kept as pending and nothing '
                                               f'is registered until this is resolved.'})
    try:
        _base._node()
    except ActivationError as e:
        out.append({'code': e.code, 'message': f'{e} You can still save: the approval is kept as pending.'})
    if _base.passphrase_backed():
        out.append({'code': 'passphrase_vault', 'message': _base.PASSPHRASE_NOTICE})
    return out


def _norm(p) -> str:
    return str(p).replace('\\', '/')


def _same_path(a, b) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def matches(cfg, op: dict, strict: bool = True) -> bool:
    """True when an existing server config is exactly `op`'s launch line (Python, the wrapper, the
    flags, Node, the digest directory's entry file and the arguments). The shape is checked whole:
    an `env` block (NODE_OPTIONS), an extra argument or a different program fails it.

    `strict` (the default, used for "is registered" and for the write guard) also requires every
    path to be the one this install would write now: this interpreter, THIS `tools/with-secret.py`,
    the `node` on PATH and the entry file in this install's digest directory. A line that only
    ends in the same file names (any `evil/tools/with-secret.py`, any program called `node`) is
    not the approved line. `strict=False` keeps that looser shape test for ONE use: recognising an
    earlier approval whose paths moved with the install, so a new passcode Save may replace it."""
    if not isinstance(op, dict) or op.get('ecosystem') != 'npm' or not isinstance(cfg, dict) \
            or not set(cfg) <= {'command', 'args', 'type'} or cfg.get('type') not in (None, 'stdio'):
        return False
    cmd, args = cfg.get('command'), cfg.get('args')
    flags = wrapper_flags(op)
    want = 1 + len(flags) + 2 + len(op['args'])
    if not isinstance(cmd, str) or not isinstance(args, list) or len(args) != want \
            or not all(isinstance(a, str) for a in args):
        return False
    if args[1:1 + len(flags)] != flags or args[3 + len(flags):] != op['args']:
        return False
    node, entry = args[1 + len(flags)], args[2 + len(flags)]
    if strict:
        try:
            return cmd == sys.executable and _same_path(args[0], _base.wrapper_path()) \
                and _same_path(node, _base._node()) and _same_path(entry, _artifact.entry_path(op))
        except ActivationError:                         # no wrapper / no Node here: nothing on disk can be the line
            return False
    tail = f'/mcp_custom_packages/{_artifact.digest_id(op["integrity"])}/package/{op["entry"]}'
    return (cmd == sys.executable or bool(_base._PYTHON_RE.match(_norm(cmd).rsplit('/', 1)[-1]))) \
        and _norm(args[0]).endswith('tools/with-secret.py') \
        and bool(_base._NODE_RE.match(_norm(node).rsplit('/', 1)[-1])) \
        and _norm(entry).endswith(tail)


def _read(scope: str, project_id: str | None, name: str, project_path: str | None) -> dict | None:
    rec = _mcp.read_server(scope, name, project_path=project_path, project_id=project_id)
    return rec['config'] if rec else None


def _approved_ops(op: dict) -> list[dict]:
    """The operations Desk approved for this server name: the recorded one and, while a change is
    unfinished, the one it replaces."""
    try:
        rec = _store.get(op['scope']['kind'], op['scope']['project_id'], op['server_name'])
    except _store.StoreUnreadable:                      # nothing is known to be approved: an existing server is a clash
        return []
    return [o for o in (rec.get('operation'), rec.get('replaces')) if isinstance(o, dict)
            and o.get('ecosystem') == 'npm'] if rec else []


def _was_approved(cur, op: dict) -> bool:
    return any(matches(cur, p, strict=False) for p in _approved_ops(op))


def _clash(op: dict) -> ActivationError:
    return ActivationError(f'an MCP server named "{op["server_name"]}" is already set up and is not the one you approved '
                           f'here. Clayrune does not replace it: rename or remove it in the MCP panel, or choose another '
                           f'server name, then save again.', 'server_exists', 409)


def conflict(op: dict, project_path: str | None) -> ActivationError | None:
    """A server of this name that is neither this operation nor the earlier approval of it.
    Checked before the passcode is asked and again, under the config lock, when registering."""
    cur = _read(op['scope']['kind'], op['scope']['project_id'], op['server_name'], project_path)
    if cur is None or matches(cur, op):
        return None
    return None if _was_approved(cur, op) else _clash(op)


def register(op: dict, project_path: str | None) -> dict:
    """Write the approved launch line. Never overwrites a server it did not approve. Returns
    `{'server', 'already'}`."""
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
                raise _clash(op)
        servers[name] = cfg

    try:
        if op['scope']['kind'] == 'global':
            _mcp._write_global_servers(mutate)
        else:
            _mcp._write_project_servers(project_path or '', mutate)
    except ActivationError:
        raise
    except (OSError, ValueError) as e:
        _log(f'[desk_connect] custom MCP registration failed: {type(e).__name__}', flush=True)
        raise ActivationError('the MCP configuration could not be written; see the server log',
                              'register_failed', 500) from e
    return {'server': name, 'already': state['already']}


def provision(op: dict, project_path: str | None) -> dict:
    """After the durable approval: verified package, then registration. Never raises. The result
    is `{'state': 'registered'|'pending_runtime'|'setup_failed', 'code', 'message', 'server'?}`."""
    try:
        _artifact.install(op)
    except ActivationError as e:
        _log(f'[desk_connect] custom MCP {op["server_name"]} package step failed: {e.code}', flush=True)
        return {'state': 'setup_failed', 'code': e.code, 'message': str(e)}
    except Exception as e:                              # a bug here must read as a failed setup, never as success
        _log(f'[desk_connect] custom MCP {op["server_name"]} package step raised {type(e).__name__}', flush=True)
        return {'state': 'setup_failed', 'code': 'setup_failed', 'message': 'setup could not finish; see the server log'}
    try:
        _manifest.record(op)                            # what the human approved, file by file (detect-only drift check)
    except Exception as e:                              # never blocks the approval: the card then reads `not_recorded`
        _log(f'[desk_connect] custom MCP {op["server_name"]} package manifest not recorded: {type(e).__name__}', flush=True)
    try:
        done = register(op, project_path)
    except ActivationError as e:
        if e.code in _PENDING_CODES:
            return {'state': 'pending_runtime', 'code': e.code,
                    'message': f'Saved and approved. {e} The package is verified on disk; nothing is registered or '
                               f'started yet.'}
        _log(f'[desk_connect] custom MCP {op["server_name"]} registration failed: {e.code}', flush=True)
        return {'state': 'setup_failed', 'code': e.code, 'message': str(e)}
    except Exception as e:
        _log(f'[desk_connect] custom MCP {op["server_name"]} registration raised {type(e).__name__}', flush=True)
        return {'state': 'setup_failed', 'code': 'setup_failed', 'message': 'setup could not finish; see the server log'}
    return {'state': 'registered', 'code': '', 'server': done['server'],
            'message': f'Registered as the MCP server "{done["server"]}". It starts the first time an agent session uses '
                       f'it; nothing has been run yet.'}


def derive_state(rec: dict, project_path: str | None) -> dict:
    """The truthful state of a recorded approval right now: `registered` (the config is the approved
    line, the package is on disk and every credential entry exists), `pending_runtime` / `setup_failed`
    (saved, not registered), `changed` (the config is not what was approved), `missing` (no config),
    `package_missing`, or `credential_missing`. Never raises."""
    op = rec['operation']
    try:
        cur = _read(rec['scope'], rec['project_id'], rec['server_name'], project_path)
    except (OSError, ValueError) as e:
        _log(f'[desk_connect] custom MCP state unreadable: {type(e).__name__}', flush=True)
        return {'state': 'unknown', 'message': 'The MCP configuration could not be read.'}
    if cur is None:
        if rec.get('state') in ('pending_runtime', 'setup_failed'):
            return {'state': rec['state'], 'code': rec.get('code', ''),
                    'message': 'Approved and saved, but not registered.'}
        return {'state': 'missing', 'message': 'The approved server is no longer in the MCP configuration.'}
    if not matches(cur, op):
        return {'state': 'changed', 'message': 'The MCP configuration no longer matches what was approved. Connect it '
                                               'again and approve the change.'}
    if not _artifact.is_installed(op):
        return {'state': 'package_missing', 'message': 'The approved package is not on disk. Save again to restore it.'}
    files = _manifest.check(rec)
    if files['status'] == 'changed':
        return {'state': 'changed', 'code': 'package_files_changed', 'package_files': files,
                'message': 'The package files on disk no longer match what was approved. Connect it again and approve '
                           'the change; saving records the files as they are now.'}
    try:
        have = {s['name'] for s in _vault.list_secrets()}
    except Exception as e:
        _log(f'[desk_connect] vault listing failed: {type(e).__name__}', flush=True)
        have = set()
    gone = [c['vault'] for c in op['credentials'] if c['vault'] not in have]
    if gone:
        return {'state': 'credential_missing', 'message': f'Secrets has no entry named {", ".join(gone)}.', 'missing': gone}
    out = {'state': 'registered', 'message': 'Registered. It starts the first time an agent session uses it.',
           'package_files': files}
    if _base.passphrase_backed():
        out['notice'] = _base.PASSPHRASE_NOTICE
    return out
