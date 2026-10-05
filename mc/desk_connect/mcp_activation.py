"""Turning an approved curated MCP package into a registered server (docs/
DESK_CONNECT_BY_URL_SPEC.md, slice 4).

Runs only after the one Save has been accepted: the human check and the passcode
are in the route, the pins were revalidated by the provider, and the credential
(if one was typed) is already in the vault. Three things happen here, in order, and
the first two can refuse:

    1. the pin check   `npm view <package>@<version> dist.integrity` against the
                       PUBLIC registry (never the machine's configured one) must
                       equal the hash the catalogue pins. It reads metadata and
                       runs no package code. A package the registry now serves
                       differently from what was reviewed is not registered.
    2. the launch line built from the catalogue entry alone (never from a request):
                       python tools/with-secret.py --raw --env VAR=<vault name> --
                       <npx> -y <package>@<exact version>
                       so the config carries a vault NAME, never a value: the
                       wrapper resolves it into the child's environment when the
                       server starts. `--raw` because an MCP stdio server needs live
                       stdin and stdout.
    3. registration    `mc.mcp.write_server` (global scope, never overwriting).

Nothing here launches the package. `npx` runs later, when an agent session starts
the server, so the first launch is DEFERRED to a session and gated by what this
module wrote: a pinned version, a vault name, and nothing else. A session that
cannot unlock the vault (a passphrase-locked vault is only unlocked in the server's
own memory) makes the wrapper exit 2 and the server fail to start, which is the
safe direction.

The failures are not rolled back as a transaction (spec, "Explicit partial-success
boundary"): the caller reports "Saved; setup failed" and keeps the credential.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mc import mcp as _mcp
from mc.core import _log

PUBLIC_REGISTRY = 'https://registry.npmjs.org/'
PIN_CHECK_TIMEOUT = 60           # seconds: a metadata read, not an install (the five-minute install ceiling is not needed)
_SECRETISH_ENV = re.compile(r'(TOKEN|SECRET|PASSWORD|PASSPHRASE|API_?KEY|CREDENTIAL)', re.I)


class ActivationError(ValueError):
    """A refusal with a short machine `code` and the HTTP status the route would use."""

    def __init__(self, message: str, code: str = 'activation_failed', status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _which(name: str) -> str | None:
    """Indirection so tests can supply a tool that is not on this machine."""
    return shutil.which(name)


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


def launch_config(entry: dict) -> dict:
    """The config written for `entry`. Built from the reviewed catalogue entry only."""
    npx = _which('npx')
    if not npx:
        raise ActivationError('Node.js (npx) was not found on this computer. Install Node.js, then save again.',
                              'node_missing', 409)
    cred = entry['credential']
    return {'command': sys.executable,
            'args': [str(wrapper_path()), '--raw', '--env', f'{cred["env"]}={cred["vault"]}', '--',
                     npx, '-y', f'{entry["package"]}@{entry["version"]}']}


def is_ours(cfg, entry: dict) -> bool:
    """True when an existing server config is this entry's launch line (the same
    pinned package behind the same wrapper), whatever absolute paths it was written
    with: the case of a retry after a partial save."""
    if not isinstance(cfg, dict):
        return False
    args = cfg.get('args')
    cred = entry['credential']
    return (isinstance(args, list) and 'env' not in cfg and 'headers' not in cfg
            and f'{entry["package"]}@{entry["version"]}' in args
            and f'{cred["env"]}={cred["vault"]}' in args
            and any(str(a).replace('\\', '/').endswith('tools/with-secret.py') for a in args))


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


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if not _SECRETISH_ENV.search(k)}


def check_pin(entry: dict) -> None:
    """The public registry still serves exactly the reviewed build. Raises
    ActivationError. Reads metadata only: no package code runs."""
    npm = _which('npm')
    if not npm:
        raise ActivationError('Node.js (npm) was not found on this computer, so the package could not be checked. '
                              'Install Node.js, then save again.', 'node_missing', 409)
    spec = f'{entry["package"]}@{entry["version"]}'
    with tempfile.TemporaryDirectory(prefix='clayrune-pin-') as cwd:       # no project .npmrc is read
        try:
            proc = subprocess.run([npm, 'view', spec, 'dist.integrity', '--json', '--registry', PUBLIC_REGISTRY],
                                  cwd=cwd, env=_clean_env(), capture_output=True, text=True, encoding='utf-8',
                                  errors='replace', timeout=PIN_CHECK_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise ActivationError(f'the package registry did not answer within {PIN_CHECK_TIMEOUT} seconds, so '
                                  f'{spec} could not be checked. Save again later.', 'pin_check_timeout', 504) from e
        except OSError as e:
            raise ActivationError('the package check could not be started (npm failed to run)', 'pin_check_failed', 502) from e
    if proc.returncode != 0:
        _log(f'[desk_connect] npm view {spec} exited {proc.returncode}', flush=True)
        raise ActivationError(f'the package registry could not confirm {spec} (npm exited with {proc.returncode}). '
                              f'Save again later.', 'pin_check_failed', 502)
    try:
        got = json.loads(proc.stdout.strip() or 'null')
    except ValueError:
        got = None
    if got != entry['integrity']:
        raise ActivationError(f'the registry now serves {spec} with a different checksum than the one that was '
                              f'reviewed, so it was NOT registered. Nothing from it was run.', 'pin_mismatch', 409)


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
    """The post-commit provisioning: pin check, then registration. Never raises:
    the outcome is `{'setup': {'state': 'done'|'failed', 'message', 'server'?, 'code'?}}`."""
    try:
        check_pin(entry)
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
