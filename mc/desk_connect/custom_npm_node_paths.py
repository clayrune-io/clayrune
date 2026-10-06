"""The folders Node itself searches for packages, asked of Node (docs/DESK_SERVICE_PROFILES_SPEC.md, U2b audit
follow-up).

Besides every `<folder>/node_modules` up the tree (`custom_npm_gate.ancestor_problem`), Node searches a short
global list: `$HOME/.node_modules`, `$HOME/.node_libraries`, `<prefix>/lib/node` and every `NODE_PATH` entry.
Code in any of them would be loaded for a package the approved tree does not hold, with the server's secrets in
the environment. The launch gate asks Node for that list instead of guessing it (`module.globalPaths`), with the
environment the server will get (the launch line strips `NODE_OPTIONS` and `NODE_PATH` through `with-secret
--unset`, so neither reaches the server or this probe), and refuses when any listed folder exists. A probe that
fails, times out or prints something else refuses too: a check that cannot tell is not a pass.

Pure apart from the one `node` start in `run_probe`, which tests replace.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

PROBE_TIMEOUT = 2.0
PROBE_SOURCE = "process.stdout.write(JSON.stringify(require('module').globalPaths))"
_MAX_OUT = 64 * 1024
_MAX_PATHS = 64
_SHOWN = 4


class ProbeError(RuntimeError):
    """Node could not tell us its search list."""


def child_env(strip: list[str], environ=None) -> dict:
    """The environment the server will have: this one minus the names the launch line `--unset`s."""
    src = os.environ if environ is None else environ
    drop = {n.upper() if os.name == 'nt' else n for n in strip}
    return {k: v for k, v in src.items() if (k.upper() if os.name == 'nt' else k) not in drop}


def node_of(rest: list[str], env: dict) -> str:
    """The program the launch line runs: the word after the credential wrapper's `--`. A line with no `--`
    (not one Clayrune writes) falls back to the first `node` on the server's PATH; a `node` that is not there
    makes the probe fail, which refuses."""
    if '--' in rest and rest.index('--') + 1 < len(rest):
        return rest[rest.index('--') + 1]
    return shutil.which('node', path=env.get('PATH')) or 'node'


def run_probe(node: str, env: dict) -> list[str]:
    """Node's `module.globalPaths`. Raises ProbeError for anything but a clean list of strings."""
    try:
        done = subprocess.run([node, '-e', PROBE_SOURCE], env=env, capture_output=True, timeout=PROBE_TIMEOUT,
                              stdin=subprocess.DEVNULL, check=False)
    except subprocess.TimeoutExpired as e:
        raise ProbeError('Node did not answer within 2 seconds') from e
    except (OSError, ValueError) as e:
        raise ProbeError(f'Node could not be started ({type(e).__name__})') from e
    if done.returncode != 0 or len(done.stdout) > _MAX_OUT:
        raise ProbeError('Node did not give its search list')
    try:
        paths = json.loads(done.stdout.decode('utf-8'))
    except ValueError as e:
        raise ProbeError('Node gave an unreadable search list') from e
    if not isinstance(paths, list) or len(paths) > _MAX_PATHS or not all(isinstance(p, str) and p for p in paths):
        raise ProbeError('Node gave an unreadable search list')
    return paths


def _tail(path: str) -> str:
    """The end of a long path: the folder's own name is the part that identifies it."""
    return path if len(path) <= 80 else '...' + path[-77:]


def _shown(paths: list[str]) -> str:
    text = ', '.join(_tail(''.join(c if c.isprintable() else '?' for c in p)) for p in paths[:_SHOWN])
    return text + (f' and {len(paths) - _SHOWN} more' if len(paths) > _SHOWN else '')


def problem(op: dict, rest: list[str]) -> dict | None:
    """None when no folder in Node's own search list exists, else `{code, message, paths}` (`dependency_changed`,
    the code every launch refusal uses). Never raises."""
    try:
        env = child_env(list(op.get('strip_env') or ()))
        node = node_of(rest, env)
        found = [p for p in run_probe(node, env) if Path(p).exists() or Path(p).is_symlink()]
    except ProbeError as e:
        return {'code': 'dependency_changed', 'paths': [],
                'message': f'Clayrune could not ask Node where it searches for packages ({e}), so it cannot tell '
                           f'whether code outside the approved package could be loaded.'}
    except Exception as e:                                  # noqa: BLE001 - a check that cannot tell must refuse
        return {'code': 'dependency_changed', 'paths': [],
                'message': f'Clayrune could not check where Node searches for packages ({type(e).__name__}).'}
    if not found:
        return None
    return {'code': 'dependency_changed', 'paths': found,
            'message': f'Node searches the folder {_shown(found)} for any package the approved one does not hold and '
                       f'would run what it finds with this server\'s secrets, so the server will not start until it '
                       f'is removed.'}
