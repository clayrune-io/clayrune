"""Stand-in processes for tests/test_agent_shim.py (MC-1150 P1). Not a test
module (pytest only collects test_*.py).

Roles
  child   stand-in for an engine CLI. Default: heartbeat + echo stdin lines,
          exit on stdin EOF (what a Mode B CLI does). Flags:
            --lines N [--interval S] [--rc R]   print N lines, exit R
            --silent                            print nothing; exit on stdin EOF
            --cat                               read stdin to EOF, print it, exit
            --env NAME                          print NAME=<value> first
  server  stand-in for the Clayrune server in the survival matrix: waits for
          the `go` file (so the test can put it in a Job object first), spawns
          the shim in the given way, records the outcome in server.json, then
          either os._exit(0)s (what the restart path does) or sleeps.

The server role imports the real client/trampoline, so the `robust` variant
exercises the production spawn recipe, not a copy of it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY_BASE = getattr(sys, '_base_executable', sys.executable)

_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def role_child(argv):
    sys.stdout.reconfigure(newline='\n')       # byte-exact lines on Windows too
    flags = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--lines', '--interval', '--rc', '--env'):
            flags[a] = argv[i + 1]
            i += 2
        else:
            flags[a] = True
            i += 1
    if '--env' in flags:
        print(f'{flags["--env"]}={os.environ.get(flags["--env"], "<unset>")}', flush=True)
    if '--cat' in flags:
        data = sys.stdin.read()
        print('got:' + data, end='', flush=True)
        return 0
    if '--lines' in flags:
        for n in range(int(flags['--lines'])):
            print(f'line {n}', flush=True)
            time.sleep(float(flags.get('--interval', 0.05)))
        return int(flags.get('--rc', 0))
    if '--silent' in flags:
        for _ in sys.stdin:
            pass
        return 0

    def reader():
        for line in sys.stdin:
            print('echo:' + line.rstrip('\n'), flush=True)
        print('stdin-EOF', flush=True)
        os._exit(0)

    threading.Thread(target=reader, daemon=True).start()
    n = 0
    end = time.time() + 180                    # safety net: never outlive a failed cleanup
    while time.time() < end:
        print(f'hb {n}', flush=True)
        n += 1
        time.sleep(0.5)
    return 0


def _spawn_variant(variant, sdir, log):
    sys.path.insert(0, str(REPO))
    from mc import agent_shim_client as client
    shim_py = str(REPO / 'mc' / 'agent_shim.py')
    tramp_py = str(REPO / 'mc' / 'agent_shim_tramp.py')
    win = sys.platform == 'win32'
    quiet = dict(close_fds=True, stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if variant == 'robust':                    # the production recipe
        r = client.spawn_detached(sdir, log=log)
        return {'ok': r.ok, 'branch': r.branch, 'err': r.error}
    if variant == 'plain':                     # today's flags: a live child of the server
        subprocess.Popen([PY_BASE, shim_py, sdir],
                         **({'creationflags': _CREATE_NO_WINDOW} if win else {}))
    elif variant == 'detached':
        subprocess.Popen([PY_BASE, shim_py, sdir], **quiet,
                         **({'creationflags': _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP}
                            if win else {'start_new_session': True}))
    elif variant == 'setsid':                  # POSIX: own session, still a child of the server
        subprocess.Popen([sys.executable, shim_py, sdir], start_new_session=True, **quiet)
    elif variant == 'breakaway':
        subprocess.Popen([PY_BASE, shim_py, sdir], **quiet,
                         creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
                         | _CREATE_BREAKAWAY_FROM_JOB)
    elif variant == 'double':                  # trampoline, but no breakaway
        subprocess.Popen([PY_BASE, tramp_py, sdir], creationflags=_CREATE_NO_WINDOW)
    elif variant == 'wmi':                     # parent = WMI service, outside any job
        cmd = ' '.join(f'"{a}"' for a in [PY_BASE, shim_py, sdir])
        ps = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
              f"-Arguments @{{CommandLine='{cmd}'}}; $r.ProcessId")
        subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True,
                       creationflags=_CREATE_NO_WINDOW, timeout=60)
    else:
        raise SystemExit(f'bad variant {variant}')
    return {'ok': True, 'branch': variant, 'err': None}


def role_server(sdir, variant, self_exit):
    d = Path(sdir)
    while not (d / 'go').exists():
        time.sleep(0.05)

    def log(msg):
        with open(d / 'server.log', 'a', encoding='utf-8') as f:
            f.write(msg + '\n')

    try:
        out = _spawn_variant(variant, str(d), log)
    except OSError as e:                       # e.g. breakaway into a no-breakaway job
        out = {'ok': False, 'branch': variant, 'err': f'{type(e).__name__}: {e}'}
    out['pid'] = os.getpid()
    (d / 'server.json').write_text(json.dumps(out))
    for _ in range(200):
        if (d / 'shim.json').exists():
            break
        time.sleep(0.1)
    if self_exit == '1':
        time.sleep(1.0)
        os._exit(0)                            # what _perform_server_restart_async does
    time.sleep(120)


if __name__ == '__main__':
    role = sys.argv[1]
    if role == 'child':
        sys.exit(role_child(sys.argv[2:]))
    elif role == 'server':
        role_server(sys.argv[2], sys.argv[3], sys.argv[4])
