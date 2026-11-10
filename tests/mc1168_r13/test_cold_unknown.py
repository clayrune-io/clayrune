"""Actual initial-dispatch guard with a transient snapshot read failure."""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
from conftest import _stub_claude_fence_self_test
import pytest
from test_dispatch_spawn_save_race import env as dispatch_env


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('read_fault', [False, True])
def test_cold_dispatch_refuses_unknown_even_when_snapshot_read_fails(dispatch_env, tmp_path, monkeypatch, streaming, read_fault):
    import server
    from mc import state
    ar = dispatch_env['ar']
    data = tmp_path / 'projects'
    data.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data)
    monkeypatch.setattr(ar, '_load_agent_log', server._load_agent_log)
    monkeypatch.setitem(state.CONFIG, 'use_streaming_agent', streaming)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('', 0))
    monkeypatch.setattr(ar, '_fragile_resume_target', lambda *a, **k: False)
    sid = 'unknown-scheduled-reuse'
    ar._save_agent_log('p1', [{'session_id': sid, 'task': 'previous cycle', 'status': 'in_progress',
        'summary': '', 'delivery_attempt_id': 'unresolved-owner', 'launch_outcome': 'unknown',
        'claude_session_id': 'native-conversation', 'provider': 'claude',
        'trigger_type': 'schedule', 'trigger_id': 'schedule1', 'started_at': '2026-11-08T00:00:00Z'}])
    original = ar.read_text_with_retry
    faults = []

    def read(path, *args, **kwargs):
        # Fail only the first launch snapshot read, then restore real I/O.
        caller = inspect.currentframe().f_back.f_back
        if read_fault and not faults and caller.f_code.co_name == 'reserve':
            faults.append(True)
            raise PermissionError('transient snapshot read refusal after retries')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(ar, 'read_text_with_retry', read)
    error = None
    try:
        ar._dispatch_agent_internal('p1', 'scheduled retry', resume_id='native-conversation',
            reuse_session_id=sid, trigger_type='schedule', trigger_id='schedule1')
    except (RuntimeError, OSError) as exc:
        error = str(exc)
    print({'streaming': streaming, 'read_fault': read_fault, 'injected': faults,
           'spawns': len(dispatch_env['spawned']), 'error': error, 'rows': ar._load_agent_log('p1')})
    assert faults == ([True] if read_fault else [])
    assert not dispatch_env['spawned'], 'cold dispatch launched over an unresolved durable attempt'
    assert ar._load_agent_log('p1')[0]['launch_outcome'] == 'unknown'
