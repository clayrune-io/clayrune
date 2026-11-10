"""MC-1150 P2 review fixes (Fenn's BLOCK on ad73ef94), one regression test each.

Blockers 1 and 2 are on the flag-OFF path that every install runs; 3 and 5 are
flag ON. Blocker 4 (an uncertain launch must stay owned) needs a design
decision (Dave chose option C: terminate the uncertain shim and CLI, or
refuse the session until they are confirmed gone).

Every interleaving here is injected on purpose and labelled; no real CLI is
launched and no process is signalled (the shim is driven in-process).

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_spawn_review_fixes.py -q
"""
from __future__ import annotations

import ast
import subprocess
import sys
import threading
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_runtime as ar  # noqa: E402
from mc import agent_shim as H  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402
from mc import agent_spawn as S  # noqa: E402
from mc import agent_uncertain_launch as U  # noqa: E402
from mc import steward_role_dispatch as SRD  # noqa: E402
from mc.resume_context import write_context_prompt  # noqa: E402

from tests.test_agent_shim import wait_for  # noqa: E402
from tests.test_agent_spawn import _Popen, _on, root  # noqa: E402,F401  (root is a fixture)

FLAG = 'agent_survive_restart_enabled'
ON = {FLAG: True}


# ───────── 1. flag OFF: each site keeps exactly its pre-P2 creation flags ────────
def _spawn_calls(path: str):
    tree = ast.parse((REPO / path).read_text(encoding='utf-8'))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and (getattr(n.func, 'id', getattr(n.func, 'attr', None)) == 'spawn_engine_proc'
                 or (isinstance(n.func, ast.Attribute) and n.func.attr == 'launch'
                     and isinstance(n.func.value, ast.Name)
                     and n.func.value.id == '_launch_transaction'
                     and len(n.args) >= 2 and isinstance(n.args[1], ast.Name)
                     and n.args[1].id == 'spawn_engine_proc'))]


def test_the_claude_route_sites_pass_the_routes_own_flags():
    # server.py injects CREATE_NO_WINDOW|CREATE_NEW_PROCESS_GROUP into agent_routes;
    # agent_runtime's constant is CREATE_NO_WINDOW alone. The routes must hand theirs over.
    calls = _spawn_calls('mc/blueprints/agent_routes.py')
    assert len(calls) == 11          # 5 Mode A (P2) + 6 Mode B / mid-turn roll (P3)
    for call in calls:
        kw = {k.arg: ast.unparse(k.value) for k in call.keywords}
        assert kw.get('creationflags') == '_POPEN_FLAGS', ast.unparse(call)[:80]
        assert kw.get('startupinfo') == '_STARTUPINFO', ast.unparse(call)[:80]


def test_the_runtime_sites_keep_the_runtime_default_flags():
    calls = _spawn_calls('mc/agent_runtime.py')
    assert len(calls) == 9
    assert not any(k.arg in ('creationflags', 'startupinfo') for c in calls for k in c.keywords)


def test_the_factory_hands_the_callers_flags_to_popen_and_defaults_to_the_runtimes(root, monkeypatch):
    _on(False)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    route_flags = ar._POPEN_FLAGS | 0x200            # CREATE_NEW_PROCESS_GROUP, as server.py sets it
    route_si = object()
    S.spawn_engine_proc(['x'], session_id='f1', engine='claude', creationflags=route_flags, startupinfo=route_si)
    S.spawn_engine_proc(['x'], session_id='f2', engine='qwen')
    (_, a), (_, b) = _Popen.calls
    assert a['creationflags'] == route_flags and a['startupinfo'] is route_si
    assert b['creationflags'] == ar._POPEN_FLAGS and b['startupinfo'] is ar._STARTUPINFO


# ───────── 2. flag OFF: Gemini's session update precedes prompt delivery ─────────
def test_gemini_dispatch_keeps_a_prompt_delivery_error_when_delivery_races_the_update(root):
    """Fenn's interleaving: the delivery failure lands after `log_lines` is chosen
    but before `dict.update` installs it. The writer must not start until after."""
    _on(False)
    sd: dict = {}
    pending: list = []
    initialized: list = []
    rt = ar.GeminiRuntime()

    class BrokenTextPipe:
        def write(self, data):
            raise BrokenPipeError('probe')
    fake_proc = SimpleNamespace(stdin=BrokenTextPipe())
    real_lines = ar.TimestampedLines

    def deliver(proc, prompt, sid, *, session, context):
        action = lambda: write_context_prompt(proc.stdin, prompt, session, context)   # noqa: E731
        if initialized:
            action()
        else:
            pending.append(action)

    def init_lines(*a, **kw):
        for action in pending:                       # the writer wins the race
            action()
        initialized.append(True)
        return real_lines(*a, **kw)

    with ExitStack() as stack:
        stack.enter_context(patch.object(rt, 'resolve_binary', return_value=Path(sys.executable)))
        stack.enter_context(patch.object(rt, 'build_command', return_value=['fake']))
        stack.enter_context(patch.object(rt, '_write_prompt_async', side_effect=deliver))
        stack.enter_context(patch.object(rt, '_read_stream'))
        stack.enter_context(patch.object(ar.vendor_fence, 'check_launch'))
        stack.enter_context(patch.object(ar.vendor_fence, 'prepare_env'))
        stack.enter_context(patch.object(ar, '_inject_gemini_env'))
        stack.enter_context(patch.object(S.subprocess, 'Popen', return_value=fake_proc))
        stack.enter_context(patch.object(ar, 'TimestampedLines', side_effect=init_lines))
        rt.dispatch(project_path='.', task='message', mc_session_id='probe', session_dict=sd)
    assert any('prompt delivery failed' in str(x) for x in sd['log_lines']), list(sd['log_lines'])


def test_gemini_delivery_starts_only_after_the_proc_is_installed_in_the_session(root):
    _on(False)
    sd: dict = {}
    rt = ar.GeminiRuntime()
    seen: list = []
    fake_proc = SimpleNamespace(stdin=None)
    with ExitStack() as stack:
        stack.enter_context(patch.object(rt, 'resolve_binary', return_value=Path(sys.executable)))
        stack.enter_context(patch.object(rt, 'build_command', return_value=['fake']))
        stack.enter_context(patch.object(rt, '_write_prompt_async',
                                         side_effect=lambda *a, **k: seen.append(('proc' in sd, 'log_lines' in sd))))
        stack.enter_context(patch.object(rt, '_read_stream'))
        stack.enter_context(patch.object(ar.vendor_fence, 'check_launch'))
        stack.enter_context(patch.object(ar.vendor_fence, 'prepare_env'))
        stack.enter_context(patch.object(ar, '_inject_gemini_env'))
        stack.enter_context(patch.object(S.subprocess, 'Popen', return_value=fake_proc))
        rt.dispatch(project_path='.', task='message', mc_session_id='order', session_dict=sd)
    assert seen == [(True, True)]


# ───────── 3. flag ON: no delivery ack before the CLI has the prompt ─────────────
def _feeding_launch(feed, seen=None):
    """A stand-in for `C.launch`: write prompt.txt as the real one does, let `feed`
    play the shim, return a ready proc."""
    def launch(base, sid, *, prompt=None, **kw):
        sdir = C.session_dir(base, sid)
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / 'prompt.txt').write_bytes(prompt or b'')
        if seen is not None:
            seen()
        feed(sdir)
        return C.LaunchResult(True, 'probe', proc=SimpleNamespace(), spec={'launch_id': 'L1'})
    return launch


def _shim_feed_fails(sdir):
    """The real Shim._feed_prompt against a CLI that closed stdin."""
    class BrokenPipe:
        def write(self, data):
            raise BrokenPipeError('CLI closed stdin')

        def close(self):
            pass
    shim = H.Shim(str(sdir))
    shim.cli = SimpleNamespace(stdin=BrokenPipe())
    shim._feed_prompt()


def _shim_feed_succeeds(sdir):
    (sdir / 'prompt.txt').unlink()                   # what _feed_prompt does after the flush


def _steward_deliver(session):
    return lambda h: threading.Thread(
        target=write_context_prompt, args=(h.stdin, 'rules\nmessage', session, 'rules'), daemon=True).start()


def test_a_shim_feed_failure_is_not_acknowledged_as_a_delivery(root, tmp_path, monkeypatch):
    monkeypatch.setattr(S, '_DELIVERY_CONFIRM_S', 0.3)
    session: dict = {}
    SRD.begin_native_delivery(session)
    at_launch: list = []
    monkeypatch.setattr(C, 'launch', _feeding_launch(
        _shim_feed_fails, seen=lambda: at_launch.append(
            (session['_steward_role_ack']['ok'], session['_steward_role_ack']['done'].is_set()))))
    S.spawn_engine_proc(['fake'], session_id='ack1', engine='codex', root=root, cfg=ON,
                        deliver=_steward_deliver(session))
    assert at_launch == [(False, False)]             # nothing acknowledged before the CLI exists
    with pytest.raises(SRD.model.RoleError):
        SRD.wait_native_delivery(session, timeout=5)
    assert '_ctx_delivered_sha' not in session
    assert session['_stdin_write_uncertain'] is True
    assert 'prompt delivery failed' in (C.session_dir(root, 'ack1') / 'shim.log').read_text()


def test_a_shim_feed_success_is_acknowledged_after_the_cli_has_the_prompt(root, monkeypatch):
    session: dict = {}
    SRD.begin_native_delivery(session)
    monkeypatch.setattr(C, 'launch', _feeding_launch(_shim_feed_succeeds))
    S.spawn_engine_proc(['fake'], session_id='ack2', engine='codex', root=root, cfg=ON,
                        deliver=_steward_deliver(session))
    SRD.wait_native_delivery(session, timeout=5)
    assert '_ctx_delivered_sha' in session


def test_a_plain_delivery_records_its_context_only_when_the_feed_worked(root, monkeypatch):
    monkeypatch.setattr(S, '_DELIVERY_CONFIRM_S', 0.3)
    bad: dict = {}
    good: dict = {}
    for name, feed, session in (('p-bad', _shim_feed_fails, bad), ('p-good', _shim_feed_succeeds, good)):
        monkeypatch.setattr(C, 'launch', _feeding_launch(feed))
        S.spawn_engine_proc(['fake'], session_id=name, engine='qwen', root=root, cfg=ON,
                            deliver=(lambda s: lambda h: threading.Thread(
                                target=write_context_prompt, args=(h.stdin, 'rules\nmessage', s, 'rules'),
                                daemon=True).start())(session))
    assert wait_for(lambda: '_ctx_delivered_sha' in good, timeout=5)
    assert wait_for(lambda: bad.get('log_lines'), timeout=5)
    assert '_ctx_delivered_sha' not in bad
    assert 'prompt delivery failed' in bad['log_lines'][0]


def test_a_cli_that_exits_without_taking_the_prompt_fails_fast(root, monkeypatch):
    monkeypatch.setattr(S, '_DELIVERY_CONFIRM_S', 30.0)       # would hang the test if exit were not noticed

    def feed(sdir):
        C.write_json_atomic(sdir / 'exit.json', {'launch_id': 'L1', 'rc': 1})
    session: dict = {}
    SRD.begin_native_delivery(session)
    monkeypatch.setattr(C, 'launch', _feeding_launch(feed))
    S.spawn_engine_proc(['fake'], session_id='ack3', engine='codex', root=root, cfg=ON,
                        deliver=_steward_deliver(session))
    with pytest.raises(SRD.model.RoleError):
        SRD.wait_native_delivery(session, timeout=5)


def test_defer_delivery_holds_the_ack_until_finish_delivery(root, monkeypatch):
    session: dict = {}
    SRD.begin_native_delivery(session)
    monkeypatch.setattr(C, 'launch', _feeding_launch(_shim_feed_succeeds))
    proc = S.spawn_engine_proc(['fake'], session_id='ack4', engine='gemini', root=root, cfg=ON,
                               deliver=_steward_deliver(session), defer_delivery=True)
    assert not session['_steward_role_ack']['done'].wait(0.3)
    S.finish_delivery(proc)
    SRD.wait_native_delivery(session, timeout=5)
    S.finish_delivery(proc)                                   # once only; a second call is a no-op


def test_an_exception_from_deliver_propagates_as_an_inline_call_would(root, monkeypatch):
    monkeypatch.setattr(C, 'launch', lambda *a, **k: pytest.fail('launched after a failed delivery'))

    def deliver(h):
        raise ValueError('delivery blew up')
    with pytest.raises(ValueError, match='delivery blew up'):
        S.spawn_engine_proc(['fake'], session_id='ack5', engine='codex', root=root, cfg=ON, deliver=deliver)


# ───────── 4. flag ON: an uncertain launch is terminated, or the session refuses ──
# (Dave's option C.) Every pid here is fake and `kill_tree_if_ours`/`process_state` are
# replaced, so no real process is signalled.
def _uncertain_launch(root, sid, *, manifest):
    """A fake `C.launch` reporting an uncertain launch (readiness deadline missed). With
    `manifest`, the slow shim's shim.json lands during the call, as a late shim's would."""
    d = C.session_dir(root, sid)

    def launch(*a, **k):
        d.mkdir(parents=True, exist_ok=True)
        if manifest:
            C.write_json_atomic(d / 'shim.json', dict(schema=H.SCHEMA, launch_id='L1', cli_pid=7001,
                                                      shim_pid=7002, cli_ct=1.0, shim_ct=2.0))
        launch.calls += 1
        return C.LaunchResult(False, 'probe', cli_running=True, error='shim did not become ready',
                              spec={'launch_id': 'L1'})
    launch.calls = 0
    return d, launch


class _Procs:
    """Fake process table: pid -> alive. kill_tree_if_ours records the kill and removes it
    unless `stubborn`."""
    def __init__(self, alive, stubborn=()):
        self.alive, self.stubborn, self.killed = set(alive), set(stubborn), []

    def install(self, monkeypatch):
        def kill(pid, ct, tol=0):
            self.killed.append((pid, ct))
            if pid not in self.stubborn:
                self.alive.discard(pid)
            return True
        monkeypatch.setattr(H, 'kill_tree_if_ours', kill)
        monkeypatch.setattr(H, 'process_state', lambda pid, ct, tol=0: 'ours' if pid in self.alive else 'gone')
        monkeypatch.setattr(U, 'RESOLVE_WAIT_S', 0.2)
        monkeypatch.setattr(U, 'KILL_CONFIRM_S', 0.2)
        monkeypatch.setattr(C, '_TEARDOWN_RETRY_DEADLINE_S', 0.0)       # no background retry thread


def _no_second_cli(monkeypatch):
    monkeypatch.setattr(S, '_popen', lambda *a, **k: pytest.fail('started a CLI beside an uncertain one'))


def test_an_uncertain_launch_is_terminated_by_pid_and_fails_the_launch(root, monkeypatch):
    d, res = _uncertain_launch(root, 'unc1', manifest=True)
    procs = _Procs({7001, 7002})
    procs.install(monkeypatch)
    _no_second_cli(monkeypatch)
    monkeypatch.setattr(C, 'launch', res)
    with pytest.raises(RuntimeError, match='terminated'):
        S.spawn_engine_proc(['fake'], session_id='unc1', engine='codex', root=root, cfg=ON)
    assert sorted(procs.killed) == [(7001, 1.0), (7002, 2.0)]       # CLI and shim, by identity
    assert procs.alive == set() and not U.marker_path(d).exists()


def test_a_retry_after_an_uncertain_launch_starts_a_fresh_cli_only_once_nothing_is_left(root, monkeypatch):
    """The scenario of the first strict xfail: the slow shim wakes up after the give-up."""
    d, res = _uncertain_launch(root, 'unc2', manifest=True)
    procs = _Procs({7001, 7002})
    procs.install(monkeypatch)
    monkeypatch.setattr(C, 'launch', res)
    with pytest.raises(RuntimeError):
        S.spawn_engine_proc(['fake'], session_id='unc2', engine='codex', root=root, cfg=ON)
    assert procs.alive == set()                                      # the late shim was not left running
    launched: list = []
    monkeypatch.setattr(C, 'launch', lambda *a, **k: launched.append(1) or C.LaunchResult(
        True, 'probe', proc=SimpleNamespace(), spec={'launch_id': 'L2'}))
    S.spawn_engine_proc(['fake'], session_id='unc2', engine='codex', root=root, cfg=ON)
    assert launched == [1]


def test_an_uncertain_launch_with_no_manifest_leaves_a_marker_that_refuses_the_session(root, monkeypatch):
    d, res = _uncertain_launch(root, 'unc3', manifest=False)
    _Procs(set()).install(monkeypatch)
    monkeypatch.setattr(C, 'launch', res)
    _no_second_cli(monkeypatch)
    with pytest.raises(RuntimeError, match='not starting a second one'):
        S.spawn_engine_proc(['fake'], session_id='unc3', engine='codex', root=root, cfg=ON)
    assert U.pending(d)['launch_id'] == 'L1'
    for _ in range(2):                                              # refused again, with no plain-Popen fallback
        with pytest.raises(RuntimeError, match='unresolved'):
            S.spawn_engine_proc(['fake'], session_id='unc3', engine='codex', root=root, cfg=ON)
    assert res.calls == 1                                           # and no new shim launch either


def test_a_process_that_will_not_die_leaves_a_marker_and_the_session_stays_refused(root, monkeypatch):
    d, res = _uncertain_launch(root, 'unc4', manifest=True)
    procs = _Procs({7001, 7002}, stubborn={7002})
    procs.install(monkeypatch)
    monkeypatch.setattr(C, 'launch', res)
    _no_second_cli(monkeypatch)
    with pytest.raises(RuntimeError, match='not starting a second one'):
        S.spawn_engine_proc(['fake'], session_id='unc4', engine='codex', root=root, cfg=ON)
    assert U.pending(d) is not None and 7002 in procs.alive
    with pytest.raises(RuntimeError, match='unresolved'):
        S.spawn_engine_proc(['fake'], session_id='unc4', engine='codex', root=root, cfg=ON)


def test_the_marker_clears_once_the_pids_are_gone_and_the_session_spawns_again(root, monkeypatch):
    d, res = _uncertain_launch(root, 'unc5', manifest=True)
    procs = _Procs({7001, 7002}, stubborn={7001, 7002})
    procs.install(monkeypatch)
    monkeypatch.setattr(C, 'launch', res)
    with pytest.raises(RuntimeError):
        S.spawn_engine_proc(['fake'], session_id='unc5', engine='codex', root=root, cfg=ON)
    assert U.pending(d) is not None
    procs.alive.clear()                                              # the stubborn processes finally exit
    launched: list = []
    monkeypatch.setattr(C, 'launch', lambda *a, **k: launched.append(1) or C.LaunchResult(
        True, 'probe', proc=SimpleNamespace(), spec={'launch_id': 'L2'}))
    S.spawn_engine_proc(['fake'], session_id='unc5', engine='codex', root=root, cfg=ON)
    assert launched == [1] and U.pending(d) is None


def test_the_marker_still_refuses_the_session_when_the_flag_is_off(root, monkeypatch):
    # Reversed 2026-11-05 (Fenn's re-review, Dave's call): this used to assert the marker
    # was ignored with the flag off, which let a retry run beside an unresolved CLI.
    d = C.session_dir(root, 'unc6')
    d.mkdir(parents=True, exist_ok=True)
    U._write_marker(d, 'unc6', 'L1', 'test')
    _on(False)
    _Popen.calls = []
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    with pytest.raises(RuntimeError, match='unresolved'):
        S.spawn_engine_proc(['fake'], session_id='unc6', engine='codex', root=root)
    assert _Popen.calls == []


# ───────── 5. flag ON: a previous shim must be gone before its dir is reused ─────
def _prior_run(root, sid):
    d = C.session_dir(root, sid)
    d.mkdir(parents=True)
    old = dict(schema=H.SCHEMA, launch_id='old', cli_pid=11111, shim_pid=22222, cli_ct=1, shim_ct=2)
    C.write_json_atomic(d / 'shim.json', old)
    C.write_json_atomic(d / 'exit.json', dict(launch_id='old', rc=0))     # exit.json lands BEFORE the shim leaves
    return d, old


def test_a_previous_shim_that_is_still_exiting_is_not_overwritten(root, monkeypatch):
    d, old = _prior_run(root, 'reuse1')
    monkeypatch.setattr(S, '_PRIOR_SHIM_GONE_S', 0.2)
    monkeypatch.setattr(H, 'process_state', lambda pid, ct, tol=0: 'ours')
    monkeypatch.setattr(C, 'launch', lambda *a, **k: pytest.fail('reused a directory its shim still owns'))
    monkeypatch.setattr(S.subprocess, 'Popen', _Popen)
    _Popen.calls = []
    p = S.spawn_engine_proc(['fake'], session_id='reuse1', engine='codex', root=root, cfg=ON)
    assert isinstance(p, _Popen) and len(_Popen.calls) == 1
    assert C.read_json(d / 'shim.json') == old


def test_the_directory_is_reused_once_the_previous_shim_has_gone(root, monkeypatch):
    _prior_run(root, 'reuse2')
    states = iter(['ours', 'ours', 'gone'])
    monkeypatch.setattr(H, 'process_state', lambda pid, ct, tol=0: next(states, 'gone'))
    launched: list = []
    monkeypatch.setattr(C, 'launch', lambda *a, **k: launched.append(1) or C.LaunchResult(
        True, 'probe', proc=SimpleNamespace(), spec={'launch_id': 'n'}))
    S.spawn_engine_proc(['fake'], session_id='reuse2', engine='codex', root=root, cfg=ON)
    assert launched == [1]


def test_the_proc_binds_to_this_launchs_manifest_not_a_late_rewrite(root, monkeypatch):
    """Fenn's REUSE interleaving: the old shim's final write lands after wait_ready
    matched the new manifest and before ShimProc is built."""
    d, old = _prior_run(root, 'reuse3')
    monkeypatch.setattr(H, 'process_state', lambda pid, ct, tol=0: 'gone')

    def ready_then_old_final(sdir, launch_id, **kw):
        new = dict(old, launch_id=launch_id, cli_pid=33333, shim_pid=44444)
        C.write_json_atomic(Path(sdir) / 'shim.json', new)
        C.write_json_atomic(Path(sdir) / 'shim.json', old)    # the late, stale write
        return new
    monkeypatch.setattr(C, 'spawn_detached', lambda *a, **k: C.SpawnResult(True, 'probe'))
    monkeypatch.setattr(C, 'wait_ready', ready_then_old_final)
    proc = S.spawn_engine_proc(['fake'], session_id='reuse3', engine='codex', root=root, cfg=ON)
    assert proc.pid == 33333 and proc.shim_pid == 44444
    assert proc.manifest['launch_id'] != 'old'
