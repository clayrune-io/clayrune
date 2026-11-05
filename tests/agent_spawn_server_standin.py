"""Stand-in Clayrune server for tests/test_agent_spawn.py (MC-1150 P2). Not a
test module (pytest only collects test_*.py).

    agent_spawn_server_standin.py <shims_root> <out_json> <session_id> <lines> <interval>

Turns the survive-restart flag on, spawns a fake engine turn through the real
`spawn_engine_proc`, records the outcome in <out_json> (written atomically),
then sleeps until the test kills it, which is what a server restart does.
"""
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PY = getattr(sys, '_base_executable', sys.executable)


def main(argv):
    root, out, sid, lines, interval = argv[0], argv[1], argv[2], argv[3], argv[4]
    from mc import agent_shim_client as C
    from mc import agent_spawn as S
    from mc import state
    state.CONFIG['agent_survive_restart_enabled'] = True
    S.set_root(root)
    cli = [PY, str(REPO / 'tests' / 'fake_engine_cli.py'), 'killme',
           '--lines', lines, '--interval', interval]
    proc = S.spawn_engine_proc(cli, session_id=sid, engine='gemini')
    rec = {'pid': os.getpid(), 'shim_proc': isinstance(proc, C.ShimProc)}
    tmp = out + '.tmp'
    Path(tmp).write_text(json.dumps(rec), encoding='utf-8')
    os.replace(tmp, out)
    while True:
        time.sleep(1)


if __name__ == '__main__':
    main(sys.argv[1:])
