"""R45 (MC-1179, docs/_review/2026-11-16_02_agent_routes.md): the Hivemind
orchestrator's `on_process_exit` hook is the only thing that frees its slot in
`_hivemind_orchestrating`, and `_spawn_orchestrator` returns None while the id is
still in that set. With `use_streaming_agent` on (the default), the orchestrator
was dispatched in Mode B, whose session dict never stored `_runtime_callbacks`, so
the hook never ran and the hivemind stalled. The seam's `mode='A'` argument was
not read by `server._claude_dispatch_hook`.

Real `_hm_dispatch_orchestrator` -> `ClaudeRuntime.dispatch` ->
`_claude_dispatch_hook` -> `_dispatch_agent_internal`; only `subprocess.Popen` and
the threads are faked (same harness as test_hivemind_worktree_dispatch_hook.py).
"""
from pathlib import Path

from mc import state
from mc.blueprints import agent_routes as ar
from mc.blueprints import hivemind_routes as hm

from tests.test_hivemind_worktree_dispatch_hook import (  # noqa: F401
    _SyncThread, _manifest, env, repo)


def _start_guardian_inert():
    # ensure_guardian() starts a forever-loop thread; start it before Thread is
    # swapped for the run-inline stand-in, or the test never returns.
    ar.get_manager('hmhook').ensure_guardian()


def _dispatch_orchestrator(env):
    mp = env['monkeypatch']
    _start_guardian_inert()
    mp.setattr(ar.threading, 'Thread', _SyncThread)
    mp.setattr(hm.threading, 'Thread', _SyncThread)
    hm._hm_ensure_dirs('hm1')
    hm._hm_save_manifest('hm1', _manifest())
    return hm._hm_dispatch_orchestrator('hm1', 'synthesize')


def test_orchestrator_exit_hook_frees_its_slot_with_streaming_on(env):
    env['monkeypatch'].setitem(state.CONFIG, 'use_streaming_agent', True)
    try:
        sid = _dispatch_orchestrator(env)
        assert (sid or '').startswith('hm_orch_')
        session = state.agent_sessions[sid]

        # The fake process's stdout is empty, so the reader ran to its exit path
        # inline. The slot must be free; before the fix it stayed taken forever.
        assert 'hm1' not in hm._hivemind_orchestrating

        # The orchestrator is a one-task process that must exit, so it has to be
        # Mode A; and whatever mode it runs in, the hook has to be on the dict.
        assert session['mode'] == 'A'
        assert 'on_process_exit' in session['_runtime_callbacks']
    finally:
        with hm._hivemind_orch_lock:
            hm._hivemind_orchestrating.discard('hm1')


def test_mode_b_session_dict_keeps_callbacks_and_housekeeping(env):
    """A Mode B dispatch that is handed callbacks must not drop them (the Mode A
    dict and the pending-launch record both store them)."""
    env['monkeypatch'].setitem(state.CONFIG, 'use_streaming_agent', True)
    _start_guardian_inert()
    env['monkeypatch'].setattr(ar.threading, 'Thread', _SyncThread)
    def hook(*_a):
        pass
    sid = ar._dispatch_agent_internal(
        'hmhook', 'work', trigger_type='hivemind_orchestrator', trigger_id='hm1',
        housekeeping=True, runtime_callbacks={'on_process_exit': hook})
    session = state.agent_sessions[sid]
    assert session['mode'] == 'B'
    assert session['housekeeping'] is True
    assert session['_runtime_callbacks']['on_process_exit'] is hook
