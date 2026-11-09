"""Cold scheduled Claude reuse without a spawner, after history save recovers."""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
from conftest import _stub_claude_fence_self_test
import pytest
from test_dispatch_spawn_save_race import env as dispatch_env
from test_runtime_completion_log import _assistant_line, _result_line


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('fail_save', [False, True])
def test_cold_scheduled_reuse_keeps_completion_after_pending_save_failure(dispatch_env, tmp_path, monkeypatch, streaming, fail_save):
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
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: ('', 0))
    monkeypatch.setattr(ar, '_fragile_resume_target', lambda *a, **k: False)
    sid = 'scheduled-reuse'
    original = ar._save_agent_log
    original('p1', [{'session_id': sid, 'task': 'previous cycle', 'status': 'completed',
                    'summary': 'previous answer', 'delivery_attempt_id': 'old-owner',
                    'claude_session_id': 'native-conversation', 'provider': 'claude',
                    'trigger_type': 'schedule', 'trigger_id': 'schedule1',
                    'started_at': '2026-11-08T00:00:00Z'}])
    saves = []
    def save(pid, rows):
        saves.append(bool(dispatch_env['spawned']))
        if fail_save and dispatch_env['spawned']:
            raise OSError('cold reuse postspawn pending write failed')
        return original(pid, rows)
    monkeypatch.setattr(ar, '_save_agent_log', save)
    actual = ar._dispatch_agent_internal('p1', 'scheduled task', resume_id='native-conversation',
        reuse_session_id=sid, trigger_type='schedule', trigger_id='schedule1')
    assert actual == sid
    assert saves == [False, True]  # reservation before launch, then metadata enrichment
    session = dispatch_env['sessions'][sid]
    proc = dispatch_env['spawned'][0]
    proc.stdout = io.StringIO(_assistant_line('new scheduled answer') + '\n' + _result_line() + '\n')
    proc.wait = lambda: 0
    proc._alive = False
    monkeypatch.setattr(ar, '_save_agent_log', original)
    (ar._read_agent_stream_b if streaming else ar._read_agent_stream)(proc, session)
    rows = ar._load_agent_log('p1')
    print({'streaming': streaming, 'fail_save': fail_save, 'live_token': session.get('_delivery_attempt_id'),
           'live_status': session['status'], 'rows': rows, 'lines': list(session['log_lines'])})
    assert 'new scheduled answer' in rows[0]['summary'], 'completed scheduled answer discarded after postlaunch save failure'
