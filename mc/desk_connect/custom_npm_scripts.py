"""Running an install script a person approved on the card (docs/DESK_SERVICE_PROFILES_SPEC.md,
section 6.2, slice U2b). Scripts are OFF: nothing in `custom_npm_closure` or `custom_npm_install` runs
one unless its package, script name and exact body are in the operation's `install_steps`, which the
human Save fingerprinted. This module is the only place a script is started.

A script is what npm would run at install: `preinstall`, `install` or `postinstall` of one package,
run by the system shell with that package's folder as the working directory. The environment is built
from nothing (`script_env`), never inherited: no Clayrune secret, no vault value, no `NODE_OPTIONS`, no
`npm_config_*` registry or auth, no user `.npmrc` (HOME is an empty folder inside the staging area and
the npm config files point at the null device). That is a fixed environment, not a sandbox: an approved
script has this account's file and network access, and the card says so.

Bounds: a time limit per script (the process tree is killed at it), and a capped copy of the output kept
for the server log only. A script that fails stops the install and nothing is placed.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from mc.core import _log
from mc.desk_connect.mcp_errors import ActivationError

TIMEOUT_S = 120
KEEP_OUTPUT = 2000


def script_env(step: dict, home: Path, node_dir: str, cwd: Path) -> dict:
    """The complete environment of one script. Nothing is copied from this process except what a shell
    needs to find its own system programs."""
    sep = os.pathsep
    bins = sep.join([str(cwd / 'node_modules' / '.bin'), node_dir])
    if os.name == 'nt':
        root = os.environ.get('SystemRoot') or r'C:\Windows'
        env = {'PATH': sep.join([bins, os.path.join(root, 'System32'), root]), 'SystemRoot': root,
               'COMSPEC': os.environ.get('COMSPEC') or os.path.join(root, 'System32', 'cmd.exe'),
               'PATHEXT': '.COM;.EXE;.BAT;.CMD', 'USERPROFILE': str(home), 'APPDATA': str(home / 'AppData'),
               'LOCALAPPDATA': str(home / 'AppData'), 'TEMP': str(home / 'tmp'), 'TMP': str(home / 'tmp')}
    else:
        env = {'PATH': sep.join([bins, '/usr/local/bin', '/usr/bin', '/bin']), 'TMPDIR': str(home / 'tmp')}
    env.update({'HOME': str(home), 'CI': '1', 'npm_lifecycle_event': step['script'],
                'npm_package_name': step['package'], 'npm_package_version': step['version'],
                'npm_config_userconfig': os.devnull, 'npm_config_globalconfig': os.devnull,
                'npm_config_ignore_scripts': 'true', 'npm_config_cache': str(home / 'npm-cache')})
    return env


def _command(body: str):
    """The shell command npm itself uses for a script body."""
    if os.name == 'nt':
        comspec = os.environ.get('COMSPEC') or os.path.join(os.environ.get('SystemRoot') or r'C:\Windows',
                                                           'System32', 'cmd.exe')
        return f'"{comspec}" /d /s /c "{body}"'
    return ['/bin/sh', '-c', body]


def _kill_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True, timeout=15, check=False)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception as e:                                  # noqa: BLE001
        _log(f'[desk_connect] install script tree could not be stopped: {type(e).__name__}', flush=True)
    try:
        proc.kill()
    except OSError:
        pass


def spawn(cmd, cwd: Path, env: dict, timeout: float) -> tuple[int | None, bytes]:
    """`(exit code, last output)`; the exit code is None when the time limit killed it. The real start
    of a process: tests replace it, so no test runs a shell."""
    kw: dict = {'cwd': str(cwd), 'env': env, 'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE,
                'stderr': subprocess.STDOUT}
    if os.name == 'nt':
        kw['creationflags'] = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
    else:
        kw['start_new_session'] = True
    proc = subprocess.Popen(cmd, **kw)
    tail = bytearray()

    def drain() -> None:
        assert proc.stdout is not None
        while True:
            chunk = proc.stdout.read(8192)
            if not chunk:
                return
            tail.extend(chunk)
            del tail[:-KEEP_OUTPUT]

    reader = threading.Thread(target=drain, name='mcp-script-output', daemon=True)
    reader.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.wait()
        rc = None
    reader.join(5)
    return rc, bytes(tail)


def run(step: dict, cwd: Path, home: Path, *, node_dir: str, timeout: float = TIMEOUT_S, start=None) -> None:
    """Run one approved step in `cwd`. Raises ActivationError `script_failed` when it exits non-zero or
    runs past the limit. `start(cmd, cwd, env, timeout)` is the test seam for `spawn`."""
    (home / 'tmp').mkdir(parents=True, exist_ok=True)
    label = f'{step["package"]}@{step["version"]} {step["script"]}'
    env = script_env(step, home, node_dir, cwd)
    rc, tail = (start or spawn)(_command(step['body']), cwd, env, timeout)
    if rc == 0:
        return
    shown = ''.join(c if c.isprintable() else ' ' for c in tail.decode('utf-8', 'replace'))[-300:].strip()
    _log(f'[desk_connect] install script {label} failed: {"timed out" if rc is None else f"exit {rc}"}; {shown}', flush=True)
    why = f'ran past {int(timeout)} seconds' if rc is None else f'exited with code {rc}'
    raise ActivationError(f'the install script you approved for {label} {why}, so the package was NOT saved. '
                          f'Nothing was registered.', 'script_failed', 422)
