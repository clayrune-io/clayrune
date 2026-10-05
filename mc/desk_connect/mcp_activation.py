"""Turning an approved curated MCP package into a registered server (docs/
DESK_CONNECT_BY_URL_SPEC.md, slice 4).

Runs only after the one Save has been accepted: the human check and the passcode
are in the route, the pins were revalidated by the provider, and the credential
(if one was typed) is already in the vault. Four things happen here, in order, and
the first three can refuse:

    1. the vault      a passphrase-backed vault (`secrets.key.wrapped` on disk, the
                      same test `mc/secrets_store.py` uses) cannot be read by an MCP
                      server Clayrune does not parent (MC-1047), so nothing is
                      registered and the Save reads "setup failed" with that reason.
                      The token stays stored.
    2. the package    `mcp_package_store.install`: Clayrune downloads the ONE reviewed
                      tarball itself, checks its sha512 against the catalogue and
                      unpacks it into its own directory. No npm, no npx, no `.npmrc`.
    3. the launch     line built from the catalogue entry alone (never from a request):
                      python tools/with-secret.py --raw --env VAR=<vault name> --
                      <node> <package dir>/package/<entry>
                      so the config carries a vault NAME, never a value: the wrapper
                      resolves it into the child's environment when the server starts.
                      `--raw` because an MCP stdio server needs live stdin and stdout.
    4. registration   `mc.mcp.write_server` (global scope, never overwriting).

Nothing here runs the package. Node runs it later, when an agent session starts the
server, so the first launch is DEFERRED to a session and gated by what this module
wrote: an entry file whose bytes were checked, a vault name, and nothing else.

The failures are not rolled back as a transaction (spec, "Explicit partial-success
boundary"): the caller reports "Saved; setup failed" and keeps the credential.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from mc import mcp as _mcp
from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import mcp_package_store as _store
from mc.desk_connect.mcp_errors import ActivationError

_PYTHON_RE = re.compile(r'^(python[0-9.]*w?|py)(\.exe)?$', re.I)
_NODE_RE = re.compile(r'^node(\.exe)?$', re.I)


def _which(name: str) -> str | None:
    """Indirection so tests can supply a tool that is not on this machine."""
    return shutil.which(name)


def passphrase_backed() -> bool:
    """True when the vault is passphrase-backed: the wrapped-key file is on disk, which
    is what `mc.secrets_store.lock_state()` keys on. Not `lock_state() == 'locked'`: after
    a human unlocks it the key is only in the SERVER's memory, so a server started in an
    agent session still cannot read the vault."""
    return _vault.wrapped_key_path().is_file()


def wrapper_path() -> Path:
    """`tools/with-secret.py`, the one way a vault name becomes an environment value.
    Refused in a frozen build, which does not carry it and has no `python` to run it."""
    if getattr(sys, 'frozen', False):
        raise ActivationError('this build of Clayrune does not include the credential wrapper that '
                              'starts an MCP server with its token, so it cannot activate one',
                              'wrapper_missing', 409)
    p = Path(__file__).resolve().parents[2] / 'tools' / 'with-secret.py'
    if not p.is_file():
        raise ActivationError('the credential wrapper (tools/with-secret.py) is missing from this install',
                              'wrapper_missing', 409)
    return p


def _node() -> str:
    node = _which('node')
    if not node:
        raise ActivationError('Node.js was not found on this computer, so the server could not be set up. '
                              'Install Node.js, then save again.', 'node_missing', 409)
    return node


def launch_config(entry: dict) -> dict:
    """The config written for `entry`. Built from the reviewed catalogue entry only."""
    node = _node()
    cred = entry['credential']
    return {'command': sys.executable,
            'args': [str(wrapper_path()), '--raw', '--env', f'{cred["env"]}={cred["vault"]}', '--',
                     node, str(_store.entry_path(entry))]}


def _norm(p) -> str:
    return str(p).replace('\\', '/')


def is_ours(cfg, entry: dict) -> bool:
    """True when an existing server config is exactly this entry's launch line (Python,
    the wrapper, the vault name, Node and the pinned package's entry file), whatever
    absolute paths it was written with: the case of a retry after a partial save. The
    shape is checked whole, so an `env` block (NODE_OPTIONS), an extra argument or a
    different program fails it."""
    if not isinstance(cfg, dict) or not set(cfg) <= {'command', 'args', 'type'} or cfg.get('type') not in (None, 'stdio'):
        return False
    cmd, args = cfg.get('command'), cfg.get('args')
    cred = entry['credential']
    if not isinstance(cmd, str) or not isinstance(args, list) or len(args) != 7 or not all(isinstance(a, str) for a in args):
        return False
    tail = f'/mcp_packages/{entry["id"]}/{entry["version"]}/package/{entry["entry"]}'
    return (cmd == sys.executable or bool(_PYTHON_RE.match(_norm(cmd).rsplit('/', 1)[-1]))) \
        and _norm(args[0]).endswith('tools/with-secret.py') \
        and args[1:5] == ['--raw', '--env', f'{cred["env"]}={cred["vault"]}', '--'] \
        and bool(_NODE_RE.match(_norm(args[5]).rsplit('/', 1)[-1])) \
        and _norm(args[6]).endswith(tail)


def existing(entry: dict) -> dict | None:
    """The global server named like this entry's, or None."""
    rec = _mcp.read_server('global', entry['server_name'])
    return rec['config'] if rec else None


def conflict(entry: dict) -> ActivationError | None:
    """A server with this name that is not ours: never overwritten. Checked before the
    passcode is asked (route) and again when registering."""
    cfg = existing(entry)
    if cfg is None or is_ours(cfg, entry):
        return None
    return ActivationError(f'an MCP server named "{entry["server_name"]}" is already set up and is not the reviewed '
                           f'{entry["package"]} package. Clayrune does not replace it: rename or remove it in the MCP '
                           f'panel, then save again.', 'server_exists', 409)


def is_registered(entry: dict) -> bool:
    return is_ours(existing(entry), entry)


def register(entry: dict) -> dict:
    """Write the reviewed launch line to the global MCP config. Never overwrites a
    server it did not write. Returns `{'server': name, 'already': bool}`."""
    clash = conflict(entry)
    if clash:
        raise clash
    if is_registered(entry):
        return {'server': entry['server_name'], 'already': True}
    cfg = launch_config(entry)
    try:
        _mcp.write_server(entry['server_name'], 'stdio', cfg, 'global', overwrite=False)
    except FileExistsError as e:                    # lost a race with another writer
        raise ActivationError(f'an MCP server named "{entry["server_name"]}" appeared while saving; '
                              f'nothing was replaced', 'server_exists', 409) from e
    except (OSError, ValueError) as e:
        _log(f'[desk_connect] MCP registration failed: {type(e).__name__}', flush=True)
        raise ActivationError('the MCP configuration could not be written; see the server log',
                              'register_failed', 500) from e
    return {'server': entry['server_name'], 'already': False}


def provision(entry: dict) -> dict:
    """The post-commit provisioning: vault check, verified package, registration. Never
    raises: the outcome is `{'setup': {'state': 'done'|'failed', 'message', 'server'?, 'code'?}}`."""
    try:
        if passphrase_backed():
            raise ActivationError('your Secrets vault is protected by a passphrase, and an MCP server cannot read a '
                                  'passphrase-protected vault yet (MC-1047). The token is saved. Nothing was '
                                  'downloaded or registered.', 'vault_passphrase', 409)
        _node()
        _store.install(entry)
        done = register(entry)
    except ActivationError as e:
        _log(f'[desk_connect] MCP {entry["id"]} setup failed: {e.code}', flush=True)
        return {'setup': {'state': 'failed', 'message': str(e), 'code': e.code}}
    except Exception as e:                              # a bug here must read as a failed setup, never as success
        _log(f'[desk_connect] MCP {entry["id"]} setup raised {type(e).__name__}', flush=True)
        return {'setup': {'state': 'failed', 'message': 'setup could not finish; see the server log',
                          'code': 'setup_failed'}}
    msg = (f'Registered as the MCP server "{done["server"]}". It starts the first time an agent session uses it, '
           f'and reads its token from Secrets then.')
    return {'setup': {'state': 'done', 'message': msg, 'server': done['server'], 'code': ''}}
