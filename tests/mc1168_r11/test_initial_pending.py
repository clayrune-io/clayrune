"""Initial delegated Claude dispatch: fail only its post-spawn pending save."""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
import pytest
from test_dispatch_spawn_save_race import env as dispatch_env
from test_runtime_completion_log import _assistant_line, _result_line


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('fail_save', [False, True, 'prelaunch'])
def test_initial_postspawn_save_failure_keeps_completion_owner(dispatch_env, tmp_path, monkeypatch, streaming, fail_save):
    import server
    from mc import state
    ar = dispatch_env['ar']
    data = tmp_path / 'projects'
    data.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data)
    monkeypatch.setattr(ar, '_load_agent_log', server._load_agent_log)
    monkeypatch.setitem(state.CONFIG, 'use_streaming_agent', streaming)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_run_runtime_callbacks', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    original = ar._save_agent_log
    saves = []
    def save(pid, rows):
        saves.append((bool(dispatch_env['spawned']), rows[0]['delivery_attempt_id']))
        if fail_save == 'prelaunch' or (fail_save and dispatch_env['spawned']):
            raise OSError('initial postspawn replacement failed')
        return original(pid, rows)
    monkeypatch.setattr(ar, '_save_agent_log', save)
    if fail_save == 'prelaunch':
        with pytest.raises(OSError, match='initial postspawn replacement failed'):
            ar._dispatch_agent_internal('p1', 'delegated task', notify_session='parent')
        assert dispatch_env['spawned'] == []
        assert saves[0][0] is False
        return
    sid = ar._dispatch_agent_internal('p1', 'delegated task', notify_session='parent')
    session = dispatch_env['sessions'][sid]
    before = ar._load_agent_log('p1')
    assert len(saves) == 2
    assert saves[0][0] is False and saves[1][0] is True
    assert len(dispatch_env['spawned']) == 1
    proc = dispatch_env['spawned'][0]
    proc.stdout = io.StringIO(_assistant_line('delivered answer') + '\n' + _result_line() + '\n')
    proc.wait = lambda: 0
    proc._alive = False
    monkeypatch.setattr(ar, '_save_agent_log', original)
    if streaming:
        ar._read_agent_stream_b(proc, session)
    else:
        ar._read_agent_stream(proc, session)
    after = ar._load_agent_log('p1')
    print({'streaming': streaming, 'failed': fail_save, 'saves': saves,
           'live': session['_delivery_attempt_id'], 'before': before, 'after': after,
           'lines': list(session['log_lines'])})
    assert after[0]['delivery_attempt_id'] == session['_delivery_attempt_id'], 'postspawn pending write split ownership'
    assert 'delivered answer' in after[0]['summary'], 'completion was discarded despite storage recovery'
