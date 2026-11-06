"""Stand-in Clayrune server for tests/test_agent_shim_adopt_e2e.py (MC-1150 P4).
Not a test module (pytest only collects test_*.py).

    agent_survive_server_standin.py start <work_dir> <session_id> <lines> <interval> [--hm]
    agent_survive_server_standin.py adopt <work_dir> <session_id> [--hm]

`start` is the server that dies: it turns the survive-restart flag on, dispatches
one fake Qwen turn through the REAL `QwenRuntime.dispatch` (so the session dict,
the shim spawn and the shared Mode A reader are the production ones), writes a
checkpoint, reports, and sleeps until the test kills it.

`adopt` is the server that comes back: it runs the same two boot phases
`server.py` runs (`claim_all`, then `start_all`) against the same work dir, waits
for the adopted session to finish, and reports what it saw.

Both wrap `agent_routes._log_agent_completion` to append one line per call to
<work_dir>/completions.log, so the test can count completions across both
processes. With `--hm`, a one-workstream hivemind is written under
<work_dir>/hivemind and the real orchestrator loop runs in `adopt`.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

PY = getattr(sys, '_base_executable', sys.executable)
FAKE = str(REPO / 'tests' / 'fake_engine_cli.py')
HM_ID, WS_ID = 'hm_e2e', 'ws_e2e'


def _write(path, rec):
    tmp = str(path) + '.tmp'
    Path(tmp).write_text(json.dumps(rec), encoding='utf-8')
    os.replace(tmp, path)


def _wrap_completion(work):
    import mc.blueprints.agent_routes as R
    orig = R._log_agent_completion

    def counted(session):
        with open(work / 'completions.log', 'a', encoding='utf-8') as f:
            f.write(f"{os.getpid()} {session.get('session_id')} {session.get('status')}\n")
        try:
            orig(session)                  # runs the real force_checkpoint hook first
        except Exception as e:             # the body needs a wired server; not what is tested
            with open(work / 'completions.log', 'a', encoding='utf-8') as f:
                f.write(f'body-raised {type(e).__name__}\n')
    R._log_agent_completion = counted
    return R


def _hivemind(work, sid):
    from mc.blueprints import hivemind_routes as hm
    hm.HIVEMIND_DIR = work / 'hivemind'
    d = hm.HIVEMIND_DIR / HM_ID
    (d / 'workstreams').mkdir(parents=True, exist_ok=True)
    hm._hm_save_manifest(HM_ID, {'id': HM_ID, 'status': 'active', 'project_id': 'p', 'config': {}})
    hm._hm_save_workstream(HM_ID, WS_ID, {'id': WS_ID, 'status': 'active', 'dependencies': [],
                                          'current_agent_session_id': sid})
    return hm


def start(work, sid, lines, interval, with_hm):
    from mc import agent_runtime as ar
    from mc import agent_shim_adopt as A
    from mc import agent_spawn as S
    from mc import state
    state.CONFIG['agent_survive_restart_enabled'] = True
    S.set_root(work / 'shims')
    R = _wrap_completion(work)
    rt = ar.get_runtime('qwen')
    rt.resolve_binary = lambda: PY                                          # type: ignore[assignment]
    rt.build_command = lambda *a, **k: [PY, FAKE, 'qwen', '--lines', lines,  # type: ignore[assignment]
                                        '--interval', interval]
    sd = {'session_id': sid, 'status': 'running', 'log_lines': [], 'project_id': 'p', 'task': 'e2e'}
    state.agent_sessions[sid] = sd
    if with_hm:
        _hivemind(work, sid)
    rt.dispatch(project_path=str(work), task='Hello, world.', system_prompt='', mode='A', model='',
                mc_session_id=sid, session_dict=sd, project_id='p',
                register_process=lambda *a, **k: None, callbacks=R._RUNTIME_CALLBACKS)
    assert A.write_checkpoint(sd), 'checkpoint not written'
    A.ensure_checkpointer(state.CONFIG)
    _write(work / 'start.json', {'pid': os.getpid(), 'shim_proc': type(sd['proc']).__name__ == 'ShimProc'})
    while True:
        time.sleep(1)


def adopt(work, sid, with_hm):
    from mc import agent_shim_adopt as A
    from mc import agent_spawn as S
    from mc import state
    state.CONFIG['agent_survive_restart_enabled'] = True
    S.set_root(work / 'shims')
    _wrap_completion(work)
    claimed = A.claim_all(work / 'shims')
    deps = A.default_deps()
    deps.register_process = lambda *a, **k: None   # real one needs server.py's wiring (_proc_identity)
    started = A.start_all(deps)
    hm = None
    if with_hm:
        hm = _hivemind(work, sid)
        # the orchestrator's finished-worker pass; the hivemind module reads state.agent_sessions
        from mc import state as st
        st._hivemind_orchestrator_stop.clear()
        threading.Thread(target=hm._hivemind_orchestrator_loop, daemon=True).start()
    sd = state.agent_sessions.get(sid)
    end = time.monotonic() + 40
    while sd is not None and sd.get('status') == 'running' and time.monotonic() < end:
        time.sleep(0.05)
    ws_status = None
    if hm is not None:
        end = time.monotonic() + 20
        while time.monotonic() < end:
            ws = next((w for w in hm._hm_list_workstreams(HM_ID) if w['id'] == WS_ID), {})
            ws_status = ws.get('status')
            if ws_status == 'completed':
                break
            time.sleep(0.1)
        state._hivemind_orchestrator_stop.set()
    _write(work / 'adopt.json', {
        'pid': os.getpid(), 'claimed': claimed.claimed, 'claim_skipped': claimed.skipped,
        'started': started.started, 'start_skipped': started.skipped,
        'status': sd.get('status') if sd else None,
        'lines': [str(x) for x in (sd or {}).get('log_lines', [])],
        'adopted': bool(sd and sd.get('_adopted')),
        'ws_status': ws_status,
    })


if __name__ == '__main__':
    cmd, work, sid = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    hm_flag = '--hm' in sys.argv
    if cmd == 'start':
        start(work, sid, sys.argv[4], sys.argv[5], hm_flag)
    else:
        adopt(work, sid, hm_flag)
