"""Trampoline for the agent supervisor shim (MC-1150 P1).

A detached flag alone does not save a child from `taskkill /F /T` (it follows
ParentProcessId) or from a POSIX descendants walk. The shim therefore must not
be a LIVE child of anything the server's tree-kill starts from. The server
spawns this trampoline; the trampoline spawns the real shim fully detached and
exits at once, which orphans the shim. Measured in the design doc, section 2.

Windows: shim gets DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP. The server
spawned US with CREATE_BREAKAWAY_FROM_JOB, so neither we nor the shim sit in
the server's Job object (a job-close would otherwise kill both).
POSIX:   classic double fork + setsid, std fds on /dev/null, then exec.

Entry points
  dev:      python mc/agent_shim_tramp.py <session_dir>
  frozen:   <app> --clayrune-shim tramp <session_dir>     (see app.py)
            <app> --clayrune-shim run <session_dir>       (the shim itself)

Stdlib only; the one `mc` import (agent_shim) is lazy and only for the frozen
`run` role. On failure a `spawn_error.json` is left in the session dir so the
client can report why no shim.json appeared.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import List

FLAG = '--clayrune-shim'
ROLE_TRAMP = 'tramp'
ROLE_RUN = 'run'

_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200


def shim_argv(session_dir: str) -> List[str]:
    """argv that runs the shim (role `run`) for this process image."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, FLAG, ROLE_RUN, session_dir]
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         'agent_shim.py'), session_dir]


def _record_error(session_dir: str, err: BaseException) -> None:
    try:
        with open(os.path.join(session_dir, 'spawn_error.json'), 'w', encoding='utf-8') as f:
            json.dump({'error': f'{type(err).__name__}: {err}', 'at': time.time()}, f)
    except OSError:
        pass                                           # nowhere to report; client times out


def _spawn_windows(argv: List[str]) -> None:
    subprocess.Popen(argv, creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP,
                     close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _spawn_posix(argv: List[str]) -> None:
    pid = os.fork()                                    # type: ignore[attr-defined]
    if pid > 0:
        os.waitpid(pid, 0)                             # first child exits right after its own fork
        return
    try:
        os.setsid()                                    # type: ignore[attr-defined]
        if os.fork() > 0:                              # type: ignore[attr-defined]
            os._exit(0)
        devnull = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(devnull, fd)
        os.execv(argv[0], argv)
    finally:
        os._exit(127)                                  # exec failed: never fall back into the parent


def main(args: List[str]) -> int:
    if len(args) != 1:
        return 2
    session_dir = os.path.abspath(args[0])
    try:
        argv = shim_argv(session_dir)
        if sys.platform == 'win32':
            _spawn_windows(argv)
        else:
            _spawn_posix(argv)
    except Exception as e:                             # report, then fail the exit code
        _record_error(session_dir, e)
        return 1
    return 0


def run_shim_entry(args: List[str]) -> int:
    """Handler for `<app> --clayrune-shim <role> <dir>` (frozen builds)."""
    if len(args) != 2 or args[0] not in (ROLE_TRAMP, ROLE_RUN):
        return 2
    if args[0] == ROLE_TRAMP:
        return main([args[1]])
    from mc import agent_shim
    return agent_shim.main([args[1]])


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
